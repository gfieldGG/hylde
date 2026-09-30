"""Tests for the /post pass-through endpoint and hylde.post module."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from hylde import post, server

UPSTREAM = "https://api.example.com/graphql"


def b64url(data: bytes | str | dict) -> str:
    """Encode like Hydrus' base64url: URL-safe alphabet, no padding."""
    if isinstance(data, dict):
        data = json.dumps(data)
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class TestB64Decode:
    def test_base64url_without_padding(self):
        assert post.b64decode(b64url(b"\xfb\xff\xfe?")) == b"\xfb\xff\xfe?"

    def test_standard_base64_with_padding(self):
        encoded = base64.b64encode(b"\xfb\xff\xfe?").decode()
        assert "+" in encoded or "/" in encoded
        assert post.b64decode(encoded) == b"\xfb\xff\xfe?"

    def test_form_decoded_plus_is_restored(self):
        encoded = base64.b64encode(b"\xfb\xef\xbe").decode()
        assert "+" in encoded
        assert post.b64decode(encoded.replace("+", " ")) == b"\xfb\xef\xbe"

    def test_invalid_characters_raise(self):
        with pytest.raises(post.PostRequestError):
            post.b64decode("not*base64")


class TestParseRequest:
    def test_missing_url(self):
        with pytest.raises(post.PostRequestError, match="Missing 'url'"):
            post.parse_request({})

    def test_non_http_url(self):
        with pytest.raises(post.PostRequestError, match="http"):
            post.parse_request({"url": "file:///etc/passwd"})

    def test_body_passed_through_unchanged(self):
        raw = '{"query": "{ a }",  "variables": {"id": 1}}'
        req = post.parse_request({"url": UPSTREAM, "body": b64url(raw)})
        assert req.body == raw.encode()
        assert req.headers == {"Content-Type": "application/json"}

    def test_no_body(self):
        req = post.parse_request({"url": UPSTREAM})
        assert req.body == b""

    def test_empty_optional_params_are_ignored(self):
        req = post.parse_request(
            {"url": UPSTREAM, "body": "", "headers": "", "content_type": ""}
        )
        assert req == post.parse_request({"url": UPSTREAM})

    def test_custom_content_type(self):
        req = post.parse_request(
            {
                "url": UPSTREAM,
                "body": b64url("a=1&b=2"),
                "content_type": "application/x-www-form-urlencoded",
            }
        )
        assert req.body == b"a=1&b=2"
        assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"

    def test_headers_merged_and_override_content_type(self):
        headers = {"Authorization": "Bearer x", "Content-Type": "text/plain"}
        req = post.parse_request({"url": UPSTREAM, "headers": b64url(headers)})
        assert req.headers == headers

    @pytest.mark.parametrize(
        "headers", [b64url("[1]"), b64url('{"a": 1}'), b64url("{nope")]
    )
    def test_invalid_headers(self, headers):
        with pytest.raises(post.PostRequestError, match="headers"):
            post.parse_request({"url": UPSTREAM, "headers": headers})

    def test_variables_merged_into_body(self):
        body = {"query": "q", "variables": {"id": "old", "keep": True}}
        req = post.parse_request(
            {
                "url": UPSTREAM,
                "body": b64url(body),
                "var_id": "123",
                "varjson_page": "2",
            }
        )
        assert json.loads(req.body) == {
            "query": "q",
            "variables": {"id": "123", "keep": True, "page": 2},
        }

    def test_variables_without_body(self):
        req = post.parse_request({"url": UPSTREAM, "var_q": 'a "quoted" term'})
        assert json.loads(req.body) == {"variables": {"q": 'a "quoted" term'}}

    def test_variables_with_null_body_variables(self):
        body = {"query": "q", "variables": None}
        req = post.parse_request(
            {"url": UPSTREAM, "body": b64url(body), "varjson_x": "null"}
        )
        assert json.loads(req.body)["variables"] == {"x": None}

    def test_variables_json_content_type_with_suffix(self):
        req = post.parse_request(
            {
                "url": UPSTREAM,
                "content_type": "application/graphql+json; charset=utf-8",
                "var_a": "b",
            }
        )
        assert json.loads(req.body) == {"variables": {"a": "b"}}

    def test_variables_require_json_content_type(self):
        with pytest.raises(post.PostRequestError, match="JSON content type"):
            post.parse_request(
                {"url": UPSTREAM, "content_type": "text/plain", "var_a": "b"}
            )

    def test_variables_require_json_object_body(self):
        with pytest.raises(post.PostRequestError, match="JSON object"):
            post.parse_request({"url": UPSTREAM, "body": b64url("[]"), "var_a": "b"})

    def test_variables_require_object_body_variables(self):
        body = {"variables": [1]}
        with pytest.raises(post.PostRequestError, match="body.variables"):
            post.parse_request({"url": UPSTREAM, "body": b64url(body), "var_a": "b"})

    def test_invalid_json_variable(self):
        with pytest.raises(post.PostRequestError, match="varjson_page"):
            post.parse_request({"url": UPSTREAM, "varjson_page": "two"})

    def test_duplicate_variable(self):
        with pytest.raises(post.PostRequestError, match="more than once"):
            post.parse_request({"url": UPSTREAM, "var_a": "1", "varjson_a": "1"})

    def test_empty_variable_name(self):
        with pytest.raises(post.PostRequestError, match="variable name"):
            post.parse_request({"url": UPSTREAM, "var_": "1"})


class TestPostRoute:
    @pytest.fixture(autouse=True)
    def patch_settings(self):
        fake_settings = MagicMock()
        fake_settings.maxtimeout = 5
        with patch("hylde.post.settings", fake_settings):
            yield

    def test_invalid_request_returns_400(self):
        with server.app.test_client() as client:
            resp = client.get("/post")
        assert resp.status_code == 400
        assert b"Missing" in resp.data

    def test_forwards_post_and_passes_response_through(self):
        upstream = MagicMock(
            status_code=201,
            content=b'{"data": 1}',
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        body = {"query": "q"}
        with (
            patch("hylde.post.requests.post", return_value=upstream) as mock_post,
            server.app.test_client() as client,
        ):
            resp = client.get(
                "/post",
                query_string={"url": UPSTREAM, "body": b64url(body), "var_id": "7"},
            )

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args == (UPSTREAM,)
        assert json.loads(kwargs["data"]) == {"query": "q", "variables": {"id": "7"}}
        assert kwargs["headers"] == {"Content-Type": "application/json"}
        assert kwargs["timeout"] == 5
        assert resp.status_code == 201
        assert resp.data == b'{"data": 1}'
        assert resp.content_type == "application/json; charset=utf-8"

    def test_upstream_error_status_is_passed_through(self):
        upstream = MagicMock(status_code=404, content=b"nope", headers={})
        with (
            patch("hylde.post.requests.post", return_value=upstream),
            server.app.test_client() as client,
        ):
            resp = client.get("/post", query_string={"url": UPSTREAM})
        assert resp.status_code == 404
        assert resp.data == b"nope"
        assert resp.content_type == "application/octet-stream"

    def test_transport_failure_returns_502(self):
        with (
            patch(
                "hylde.post.requests.post",
                side_effect=requests.ConnectionError("refused"),
            ),
            server.app.test_client() as client,
        ):
            resp = client.get("/post", query_string={"url": UPSTREAM})
        assert resp.status_code == 502
        assert b"refused" in resp.data
