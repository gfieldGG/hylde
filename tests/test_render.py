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


def body(fragment: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body>{fragment}</body></html>"


class TestAbsolutizeLinks:
    def test_relative_links_resolved_against_page(self):
        page = body('<a href="/post/2">n</a><img src="img/a.jpg">')
        result = render.absolutize_links(page, PAGE)
        assert 'href="https://example.com/post/2"' in result
        assert 'src="https://example.com/gallery/img/a.jpg"' in result

    def test_media_css_and_protocol_relative(self):
        page = body(
            '<video><source src="/file.mp4"></video><a href="//cdn.example.com/y">y</a>',
            head="<style>.x{background:url(/bg.png)}</style>",
        )
        result = render.absolutize_links(page, PAGE)
        assert 'src="https://example.com/file.mp4"' in result
        assert 'href="https://cdn.example.com/y"' in result
        assert "url(https://example.com/bg.png)" in result

    def test_srcset_and_poster(self):
        page = body(
            '<img srcset="/a-1x.jpg 1x,/a-2x.jpg 2x, https://cdn.example.com/a3.jpg 3x">'
            '<video poster="/p.jpg"></video>'
        )
        result = render.absolutize_links(page, PAGE)
        assert (
            'srcset="https://example.com/a-1x.jpg 1x, https://example.com/a-2x.jpg 2x, '
            'https://cdn.example.com/a3.jpg 3x"'
        ) in result
        assert 'poster="https://example.com/p.jpg"' in result

    @pytest.mark.parametrize(
        ("base", "expected"),
        [
            ("https://cdn.example.net/x/", "https://cdn.example.net/x/f.jpg"),
            ("/media/", "https://example.com/media/f.jpg"),
            ("media/", "https://example.com/gallery/media/f.jpg"),
        ],
    )
    def test_base_href_applies(self, base, expected):
        page = body('<img src="f.jpg">', head=f'<base href="{base}">')
        assert f'src="{expected}"' in render.absolutize_links(page, PAGE)

    def test_other_schemes_and_scripts_untouched(self):
        page = body(
            '<a href="/r">r</a><a href="javascript:void(0)">js</a>'
            '<a href="data:text/plain,hi">d</a><script>var u = "/x.mp4";</script>'
        )
        result = render.absolutize_links(page, PAGE)
        assert 'href="javascript:void(0)"' in result
        assert 'href="data:text/plain,hi"' in result
        assert 'var u = "/x.mp4";' in result

    def test_page_without_relative_links_is_unchanged(self):
        page = '<html>  <body><a href="https://x.org/">x</a>\n</body></html>'
        assert render.absolutize_links(page, PAGE) == page

    def test_doctype_kept_but_not_invented(self):
        with_doctype = render.absolutize_links(
            "<!DOCTYPE html>" + body('<a href="/r">r</a>'), PAGE
        )
        without_doctype = render.absolutize_links(body('<a href="/r">r</a>'), PAGE)
        assert with_doctype.startswith("<!DOCTYPE html>")
        assert not without_doctype.lower().startswith("<!doctype")

    def test_non_ascii_and_xml_declaration(self):
        page = '<?xml version="1.0" encoding="utf-8"?>' + body(
            '<a href="/r">Größe ✓</a>', head='<meta charset="iso-8859-1">'
        )
        result = render.absolutize_links(page, PAGE)
        assert 'href="https://example.com/r"' in result
        assert "Größe ✓" in result

    @pytest.mark.parametrize("page", ["", "   "])
    def test_empty_page_is_unchanged(self, page):
        assert render.absolutize_links(page, PAGE) == page


class TestSrcsetCandidates:
    @pytest.mark.parametrize(
        ("srcset", "expected"),
        [
            ("a.jpg 1x, b.jpg 2x", [("a.jpg", "1x"), ("b.jpg", "2x")]),
            ("a.jpg", [("a.jpg", "")]),
            ("a.jpg,b.jpg 2x", [("a.jpg,b.jpg", "2x")]),  # URL keeps inner commas
            ("x.jpg, , y.jpg 100w,", [("x.jpg", ""), ("y.jpg", "100w")]),
            ("", []),
        ],
    )
    def test_parsing(self, srcset, expected):
        assert render._srcset_candidates(srcset) == expected


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

    def test_links_resolved_against_final_url(self):
        upstream = trawl_response(
            200,
            {
                "html": body('<a href="next">n</a>'),
                "statusCode": 200,
                "url": "https://example.com/moved/1",
            },
        )
        with patch("hylde.render.requests.post", return_value=upstream):
            resp = self.get(url=PAGE)
        assert b'href="https://example.com/moved/next"' in resp.data

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
