"""Tests for hylde.server module."""

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hylde import server


class TestShimRoute:
    """Tests for the /shim endpoint."""

    def test_returns_blank_page_with_200(self):
        with server.app.test_client() as client:
            resp = client.get("/shim")
        assert resp.status_code == 200
        assert resp.data == b" "


class TestHandleRequest:
    """Tests for the /file endpoint."""

    @pytest.fixture(autouse=True)
    def patch_settings(self, tmp_path):
        """Use a temp directory and tiny timeout for all server tests."""
        fake_settings = MagicMock()
        fake_settings.maxtimeout = 0.01
        fake_settings.cachedbfile = str(tmp_path / "cache.db")
        with (
            patch.object(server, "cache_dir", tmp_path),
            patch.object(server, "cache_file", tmp_path / "cache.db"),
            patch("hylde.server.settings", fake_settings),
        ):
            yield

    def test_missing_url_returns_400(self):
        with server.app.test_client() as client:
            resp = client.get("/file")
        assert resp.status_code == 400
        assert b"Missing" in resp.data

    def test_no_cache_starts_download_and_returns_429(self):
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True

        with patch("hylde.server.threading.Thread", return_value=fake_thread):
            with server.app.test_client() as client:
                resp = client.get("/file?url=http://example.com/img.jpg")

        fake_thread.start.assert_called_once()
        fake_thread.join.assert_called_once()
        assert resp.status_code == 429
        assert b"Come back later" in resp.data

    def test_active_thread_joins_and_returns_429_if_still_alive(self):
        url = "http://example.com/img.jpg"
        url_key = server.get_url_key(url)
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True
        server.active_threads[url_key] = fake_thread

        with server.app.test_client() as client:
            resp = client.get(f"/file?url={url}")

        fake_thread.join.assert_called_once()
        assert resp.status_code == 429
        assert b"retry later" in resp.data

        server.active_threads.clear()

    def test_active_thread_finishes_then_serves(self, tmp_path):
        url = "http://example.com/img.jpg"
        url_key = server.get_url_key(url)
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = False
        server.active_threads[url_key] = fake_thread

        cached = tmp_path / url_key / "img.jpg"
        cached.parent.mkdir(parents=True)
        cached.write_text("data")
        server.set_cached_file(url_key, f"{url_key}/img.jpg")

        with server.app.test_client() as client:
            resp = client.get(f"/file?url={url}")

        assert resp.status_code == 200
        assert resp.data == b"data"

        server.active_threads.clear()

    def test_retryable_cache_returns_503(self, tmp_path):
        url = "http://example.com/img.jpg"
        url_key = server.get_url_key(url)
        server.set_cached_file(url_key, "")

        with server.app.test_client() as client:
            resp = client.get(f"/file?url={url}")

        assert resp.status_code == 503
        assert b"try again" in resp.data
        assert server.get_cached_file(url_key) is None

    def test_failed_cache_returns_500(self, tmp_path):
        url = "http://example.com/img.jpg"
        url_key = server.get_url_key(url)
        server.set_cached_file(url_key, "FAILED")

        with server.app.test_client() as client:
            resp = client.get(f"/file?url={url}")

        assert resp.status_code == 500
        assert b"Failed" in resp.data
        assert server.get_cached_file(url_key) is None

    def test_missing_cached_file_returns_503(self, tmp_path):
        url = "http://example.com/img.jpg"
        url_key = server.get_url_key(url)
        server.set_cached_file(url_key, f"{url_key}/gone.jpg")

        with server.app.test_client() as client:
            resp = client.get(f"/file?url={url}")

        assert resp.status_code == 503
        assert b"missing" in resp.data
        assert server.get_cached_file(url_key) is None

    def test_serves_cached_file(self, tmp_path):
        url = "http://example.com/img.jpg"
        url_key = server.get_url_key(url)
        cached = tmp_path / url_key / "img.jpg"
        cached.parent.mkdir(parents=True)
        cached.write_text("image data")
        server.set_cached_file(url_key, f"{url_key}/img.jpg")

        with server.app.test_client() as client:
            resp = client.get(f"/file?url={url}")

        assert resp.status_code == 200
        assert resp.data == b"image data"


class TestCacheHelpers:
    """Tests for shelve cache helpers."""

    @pytest.fixture(autouse=True)
    def patch_settings(self, tmp_path):
        fake_settings = MagicMock()
        fake_settings.cachedbfile = str(tmp_path / "cache.db")
        with (
            patch.object(server, "cache_file", tmp_path / "cache.db"),
            patch("hylde.server.settings", fake_settings),
        ):
            yield

    def test_get_cached_file_returns_none_when_missing(self):
        assert server.get_cached_file("nope") is None

    def test_set_and_get_cached_file(self):
        server.set_cached_file("abc", "abc/file.txt")
        assert server.get_cached_file("abc") == "abc/file.txt"

    def test_remove_cached_file_deletes_entry(self):
        server.set_cached_file("xyz", "xyz/file.txt")
        server.remove_cached_file("xyz")
        assert server.get_cached_file("xyz") is None

    def test_remove_cached_file_deletes_actual_file(self, tmp_path):
        with patch.object(server, "cache_dir", tmp_path):
            f = tmp_path / "key" / "file.txt"
            f.parent.mkdir(parents=True)
            f.write_text("data")
            server.set_cached_file("key", "key/file.txt")
            server.remove_cached_file("key")
            assert not f.exists()

    def test_remove_cached_file_skips_empty_string(self, tmp_path):
        with patch.object(server, "cache_dir", tmp_path):
            server.set_cached_file("empty", "")
            server.remove_cached_file("empty")
            assert server.get_cached_file("empty") is None

    def test_remove_cached_file_skips_in_progress_marker(self, tmp_path):
        with patch.object(server, "cache_dir", tmp_path):
            server.set_cached_file("prog", "...")
            server.remove_cached_file("prog")
            assert server.get_cached_file("prog") is None


class TestLookInCacheDirectory:
    """Tests for look_in_cache_directory."""

    def test_returns_first_file(self, tmp_path):
        with patch.object(server, "cache_dir", tmp_path):
            d = tmp_path / "key"
            d.mkdir()
            (d / "a.txt").write_text("a")
            (d / "b.txt").write_text("b")

            result = server.look_in_cache_directory("key")

            assert result in ("key/a.txt", "key/b.txt")

    def test_returns_none_when_directory_empty(self, tmp_path):
        with patch.object(server, "cache_dir", tmp_path):
            d = tmp_path / "key"
            d.mkdir()
            assert server.look_in_cache_directory("key") is None

    def test_returns_none_when_directory_missing(self, tmp_path):
        with patch.object(server, "cache_dir", tmp_path):
            assert server.look_in_cache_directory("key") is None


class TestNormalizeUrl:
    """Tests for normalize_url."""

    def test_returns_input_unchanged(self):
        assert server.normalize_url("https://example.com") == "https://example.com"


class TestGetUrlKey:
    """Tests for get_url_key."""

    def test_returns_md5_hex(self):
        key = server.get_url_key("hello")
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_same_url_same_key(self):
        assert server.get_url_key("foo") == server.get_url_key("foo")

    def test_different_url_different_key(self):
        assert server.get_url_key("a") != server.get_url_key("b")


class TestDownloadFileHelper:
    """Tests for the server-side download_file helper."""

    @pytest.fixture(autouse=True)
    def patch_settings(self, tmp_path):
        fake_settings = MagicMock()
        fake_settings.cachedbfile = str(tmp_path / "cache.db")
        with (
            patch.object(server, "cache_dir", tmp_path),
            patch.object(server, "cache_file", tmp_path / "cache.db"),
            patch("hylde.server.settings", fake_settings),
        ):
            yield

    def test_sets_failed_on_none(self):
        url = "http://example.com"
        url_key = server.get_url_key(url)
        server.active_threads[url_key] = MagicMock()

        with patch("hylde.server.hydl.download_file", return_value=None):
            server.download_file(url, url_key)

        assert server.get_cached_file(url_key) == "FAILED"
        assert url_key not in server.active_threads

    def test_sets_empty_on_exception(self):
        url = "http://example.com"
        url_key = server.get_url_key(url)
        server.active_threads[url_key] = MagicMock()

        with patch("hylde.server.hydl.download_file", side_effect=RuntimeError("boom")):
            server.download_file(url, url_key)

        assert server.get_cached_file(url_key) == ""
        assert url_key not in server.active_threads

    def test_sets_filename_on_success(self, tmp_path):
        url = "http://example.com"
        url_key = server.get_url_key(url)
        server.active_threads[url_key] = MagicMock()

        with patch(
            "hylde.server.hydl.download_file", return_value=f"{url_key}/file.txt"
        ):
            server.download_file(url, url_key)

        assert server.get_cached_file(url_key) == f"{url_key}/file.txt"
        assert url_key not in server.active_threads

    def test_recovers_from_cache_directory(self, tmp_path):
        url = "http://example.com"
        url_key = server.get_url_key(url)
        d = tmp_path / url_key
        d.mkdir()
        (d / "recovered.txt").write_text("data")
        server.active_threads[url_key] = MagicMock()

        server.download_file(url, url_key)

        assert server.get_cached_file(url_key) == f"{url_key}/recovered.txt"
        assert url_key not in server.active_threads
