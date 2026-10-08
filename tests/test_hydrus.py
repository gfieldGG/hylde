"""Tests for hylde.hydrus, the Hydrus Client API session sharing."""

from unittest.mock import MagicMock, call, patch

import pytest

from hylde import hydrus

API = "http://hydrus:45869"
KEY = "0150d9c4"
PAGE = "https://f59.example.com/api/file/1"


@pytest.fixture
def configured():
    fake_settings = MagicMock()
    fake_settings.render.hydrus_url = API + "/"
    fake_settings.render.hydrus_key = KEY
    with patch("hylde.hydrus.settings", fake_settings):
        yield


def cookie(name: str, domain: str, **extra) -> dict:
    return {"name": name, "value": f"{name}-v", "domain": domain} | extra


class TestIsConfigured:
    @pytest.mark.parametrize(
        ("url", "key", "expected"),
        [(API, KEY, True), ("", KEY, False), (API, "", False)],
    )
    def test_requires_url_and_key(self, url, key, expected):
        fake_settings = MagicMock()
        fake_settings.render.hydrus_url = url
        fake_settings.render.hydrus_key = key
        with patch("hylde.hydrus.settings", fake_settings):
            assert hydrus.is_configured() is expected

    def test_missing_render_section(self):
        with patch("hylde.hydrus.settings", object()):
            assert hydrus.is_configured() is False


class TestSessionCookies:
    def test_rows_and_expiry(self):
        cookies = [
            cookie("sid", ".example.com", path="/", expires=-1),
            cookie("tok", "f59.example.com", path="/dl", expires=1893456000.5),
            cookie("nopath", "example.com"),
        ]
        assert hydrus.session_cookies(cookies, PAGE) == [
            ["sid", "sid-v", ".example.com", "/", None],
            ["tok", "tok-v", "f59.example.com", "/dl", 1893456000],
            ["nopath", "nopath-v", "example.com", "/", None],
        ]

    def test_cookies_for_other_hosts_are_left_out(self):
        cookies = [
            cookie("ads", ".adnetwork.com"),
            cookie("sibling", "f60.example.com"),
            cookie("suffix", "ample.com"),
            cookie("empty", ""),
        ]
        assert hydrus.session_cookies(cookies, PAGE) == []


class TestShareSession:
    def test_sets_cookies_and_user_agent_per_domain(self, configured):
        cookies = [cookie("sid", ".example.com"), cookie("tok", "f59.example.com")]
        with patch(
            "hylde.hydrus.requests.post", return_value=MagicMock(ok=True)
        ) as post:
            hydrus.share_session(cookies, "Firefox/1", PAGE)

        headers = {hydrus.ACCESS_KEY_HEADER: KEY}
        ua = {"User-Agent": {"value": "Firefox/1"}}
        assert post.call_args_list == [
            call(
                f"{API}/manage_cookies/set_cookies",
                json={
                    "cookies": [
                        ["sid", "sid-v", ".example.com", "/", None],
                        ["tok", "tok-v", "f59.example.com", "/", None],
                    ]
                },
                headers=headers,
                timeout=hydrus.API_TIMEOUT,
            ),
            call(
                f"{API}/manage_headers/set_headers",
                json={"domain": "example.com", "headers": ua},
                headers=headers,
                timeout=hydrus.API_TIMEOUT,
            ),
            call(
                f"{API}/manage_headers/set_headers",
                json={"domain": "f59.example.com", "headers": ua},
                headers=headers,
                timeout=hydrus.API_TIMEOUT,
            ),
        ]

    def test_without_user_agent_only_sets_cookies(self, configured):
        with patch(
            "hylde.hydrus.requests.post", return_value=MagicMock(ok=True)
        ) as post:
            hydrus.share_session([cookie("sid", ".example.com")], "", PAGE)
        assert post.call_count == 1

    def test_no_matching_cookies_makes_no_calls(self, configured):
        with patch("hylde.hydrus.requests.post") as post:
            hydrus.share_session([cookie("ads", ".adnetwork.com")], "Firefox/1", PAGE)
        post.assert_not_called()

    def test_api_error_raises(self, configured):
        resp = MagicMock(ok=False, status_code=403, text="missing permission")
        with (
            patch("hylde.hydrus.requests.post", return_value=resp),
            pytest.raises(hydrus.HydrusAPIError, match="403: missing permission"),
        ):
            hydrus.share_session([cookie("sid", ".example.com")], "Firefox/1", PAGE)

    def test_not_configured_raises(self):
        with (
            patch("hylde.hydrus.settings", object()),
            pytest.raises(hydrus.HydrusAPIError, match="not configured"),
        ):
            hydrus.share_session([cookie("sid", ".example.com")], "Firefox/1", PAGE)
