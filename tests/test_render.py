"""Tests for the /render endpoint and hylde.render module."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from hylde import render, server

PAGE = "https://example.com/gallery/1"
TRAWL = "http://trawl:8191"


def trawl_response(status_code: int, payload: object) -> MagicMock:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = payload
    return resp


class TestParseUrl:
    def test_missing_url(self):
        with pytest.raises(render.RenderRequestError, match="Missing 'url'"):
            render.parse_url({})

    def test_non_http_url(self):
        with pytest.raises(render.RenderRequestError, match="http"):
            render.parse_url({"url": "file:///etc/passwd"})

    def test_valid_url(self):
        assert render.parse_url({"url": PAGE}) == PAGE


class TestRenderRoute:
    @pytest.fixture(autouse=True)
    def patch_settings(self):
        fake_settings = MagicMock()
        fake_settings.maxtimeout = 55
        fake_settings.render.url = TRAWL + "/"
        fake_settings.render.timeout = 20
        with patch("hylde.render.settings", fake_settings):
            yield

    def get(self, path: str = "/render", **query):
        with server.app.test_client() as client:
            return client.get(path, query_string=query)

    def test_invalid_request_returns_400(self):
        resp = self.get()
        assert resp.status_code == 400
        assert b"Missing" in resp.data

    @pytest.mark.parametrize("path", ["/render", "/render/somesite"])
    def test_returns_rendered_html(self, path):
        upstream = trawl_response(
            200, {"html": "<html>ok</html>", "statusCode": 200, "tier": 3}
        )
        with patch("hylde.render.requests.post", return_value=upstream) as mock_post:
            resp = self.get(path, url=PAGE)

        mock_post.assert_called_once_with(
            f"{TRAWL}/scrape",
            json={"url": PAGE, "skipHttp": True, "maxTimeout": 20000},
            timeout=55,
        )
        assert resp.status_code == 200
        assert resp.data == b"<html>ok</html>"
        assert resp.content_type == "text/html; charset=utf-8"

    def test_target_status_is_passed_through(self):
        upstream = trawl_response(200, {"html": "<html>gone</html>", "statusCode": 404})
        with patch("hylde.render.requests.post", return_value=upstream):
            resp = self.get(url=PAGE)
        assert resp.status_code == 404
        assert resp.data == b"<html>gone</html>"

    def test_trawl_failure_returns_502_with_message(self):
        upstream = trawl_response(
            500, {"error": "All tiers exhausted. Last failure: http-403"}
        )
        with patch("hylde.render.requests.post", return_value=upstream):
            resp = self.get(url=PAGE)
        assert resp.status_code == 502
        assert b"All tiers exhausted" in resp.data

    def test_trawl_pool_saturated_returns_429(self):
        upstream = trawl_response(
            429, {"status": "error", "message": "Browser pool saturated, retry shortly"}
        )
        with patch("hylde.render.requests.post", return_value=upstream):
            resp = self.get(url=PAGE)
        assert resp.status_code == 429
        assert b"saturated" in resp.data

    def test_non_json_error_body(self):
        upstream = trawl_response(502, None)
        upstream.json.side_effect = ValueError("no json")
        upstream.text = "Bad Gateway"
        with patch("hylde.render.requests.post", return_value=upstream):
            resp = self.get(url=PAGE)
        assert resp.status_code == 502
        assert b"Bad Gateway" in resp.data

    def test_timeout_returns_504(self):
        with patch(
            "hylde.render.requests.post", side_effect=requests.Timeout("too slow")
        ):
            resp = self.get(url=PAGE)
        assert resp.status_code == 504
        assert b"too slow" in resp.data

    def test_connection_failure_returns_502(self):
        with patch(
            "hylde.render.requests.post",
            side_effect=requests.ConnectionError("refused"),
        ):
            resp = self.get(url=PAGE)
        assert resp.status_code == 502
        assert b"refused" in resp.data
