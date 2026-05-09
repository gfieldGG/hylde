"""Tests for hylde.wrapper module."""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hylde import wrapper


class TestZipFilesToCache:
    """Tests for _zip_files_to_cache."""

    def test_creates_zip_with_correct_name(self, tmp_path: Path):
        folder = "my_key"
        f1 = tmp_path / "a" / "file1.txt"
        f2 = tmp_path / "b" / "file2.txt"
        f1.parent.mkdir(parents=True)
        f2.parent.mkdir(parents=True)
        f1.write_text("hello")
        f2.write_text("world")

        result = wrapper._zip_files_to_cache(tmp_path, [f1, f2], folder)

        assert result == f"{folder}/{folder}.zip"
        assert (tmp_path / folder / f"{folder}.zip").exists()

    def test_preserves_relative_paths_in_zip(self, tmp_path: Path):
        base = tmp_path / "source"
        file1 = base / "sub" / "a.txt"
        file2 = base / "b.txt"
        file1.parent.mkdir(parents=True, exist_ok=True)
        file2.parent.mkdir(parents=True, exist_ok=True)
        file1.write_text("a")
        file2.write_text("b")

        wrapper._zip_files_to_cache(tmp_path, [file1, file2], "key")

        zip_path = tmp_path / "key" / "key.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        assert "source/sub/a.txt" in names
        assert "source/b.txt" in names

    def test_deletes_original_files(self, tmp_path: Path):
        file1 = tmp_path / "file1.txt"
        file1.write_text("data")

        wrapper._zip_files_to_cache(tmp_path, [file1], "key")

        assert not file1.exists()

    def test_returns_correct_relative_path(self, tmp_path: Path):
        file1 = tmp_path / "x.txt"
        file1.write_text("x")

        result = wrapper._zip_files_to_cache(tmp_path, [file1], "abc")

        assert result == "abc/abc.zip"

    def test_multiple_files_zipped_together(self, tmp_path: Path):
        f1 = tmp_path / "1.txt"
        f2 = tmp_path / "2.txt"
        f1.write_text("1")
        f2.write_text("2")

        result = wrapper._zip_files_to_cache(tmp_path, [f1, f2], "multi")

        zip_path = tmp_path / "multi" / "multi.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path, "r") as zf:
            assert len(zf.namelist()) == 2

    def test_empty_file_list_raises_value_error(self, tmp_path: Path):
        with pytest.raises(ValueError, match="empty list"):
            wrapper._zip_files_to_cache(tmp_path, [], "empty")

    def test_nested_folder_name_creates_directories(self, tmp_path: Path):
        f1 = tmp_path / "f.txt"
        f1.write_text("data")

        wrapper._zip_files_to_cache(tmp_path, [f1], "a/b/c")

        assert (tmp_path / "a" / "b" / "c" / "a" / "b" / "c.zip").exists()


class TestMoveFileToCache:
    """Tests for _move_file_to_cache."""

    def test_moves_file_to_target(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        src.write_text("data")
        target = tmp_path / "cache"

        result = wrapper._move_file_to_cache(target, src, "folder")

        assert result == "folder/src.txt"
        assert (target / "folder" / "src.txt").exists()
        assert not src.exists()

    def test_creates_parent_directories(self, tmp_path: Path):
        src = tmp_path / "file.txt"
        src.write_text("data")
        target = tmp_path / "cache"

        result = wrapper._move_file_to_cache(target, src, "a/b/c")

        assert (target / "a" / "b" / "c" / "file.txt").exists()
        assert result == "a/b/c/file.txt"

    def test_overwrites_existing_file(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        src.write_text("new")
        target = tmp_path / "cache"
        existing = target / "fld" / "src.txt"
        existing.parent.mkdir(parents=True)
        existing.write_text("old")

        result = wrapper._move_file_to_cache(target, src, "fld")

        assert existing.read_text() == "new"
        assert result == "fld/src.txt"


class TestDownloadFile:
    """Tests for download_file."""

    def test_returns_none_on_downloader_error(self, tmp_path: Path):
        mock_downloader = MagicMock()
        mock_downloader.download_url.return_value = None
        mock_downloader.__name__ = "MockDownloader"

        with (
            patch("hylde.wrapper._cache_dir", return_value=tmp_path),
            patch("hylde.wrapper.get_downloader_for_url", return_value=mock_downloader),
        ):
            result = wrapper.download_file("http://example.com", "key")

        assert result is None

    def test_returns_empty_string_on_retryable(self, tmp_path: Path):
        mock_downloader = MagicMock()
        mock_downloader.download_url.return_value = []
        mock_downloader.__name__ = "MockDownloader"

        with (
            patch("hylde.wrapper._cache_dir", return_value=tmp_path),
            patch("hylde.wrapper.get_downloader_for_url", return_value=mock_downloader),
        ):
            result = wrapper.download_file("http://example.com", "key")

        assert result == ""

    def test_moves_single_file(self, tmp_path: Path):
        src = tmp_path / "dl" / "file.txt"
        src.parent.mkdir(parents=True)
        src.write_text("data")
        mock_downloader = MagicMock()
        mock_downloader.download_url.return_value = [src]
        mock_downloader.__name__ = "MockDownloader"

        with (
            patch("hylde.wrapper._cache_dir", return_value=tmp_path),
            patch("hylde.wrapper.get_downloader_for_url", return_value=mock_downloader),
        ):
            result = wrapper.download_file("http://example.com", "key")

        assert result == "key/file.txt"
        assert (tmp_path / "key" / "file.txt").exists()

    def test_zips_multiple_files(self, tmp_path: Path):
        f1 = tmp_path / "dl" / "a.txt"
        f2 = tmp_path / "dl" / "b.txt"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f2.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("a")
        f2.write_text("b")
        mock_downloader = MagicMock()
        mock_downloader.download_url.return_value = [f1, f2]
        mock_downloader.__name__ = "MockDownloader"

        with (
            patch("hylde.wrapper._cache_dir", return_value=tmp_path),
            patch("hylde.wrapper.get_downloader_for_url", return_value=mock_downloader),
        ):
            result = wrapper.download_file("http://example.com", "key")

        assert result == "key/key.zip"
        assert (tmp_path / "key" / "key.zip").exists()

    def test_downloader_called_with_url_and_key(self, tmp_path: Path):
        mock_downloader = MagicMock()
        mock_downloader.download_url.return_value = []
        mock_downloader.__name__ = "MockDownloader"

        with (
            patch("hylde.wrapper._cache_dir", return_value=tmp_path),
            patch("hylde.wrapper.get_downloader_for_url", return_value=mock_downloader),
        ):
            wrapper.download_file("http://example.com/page", "abc123")

        mock_downloader.download_url.assert_called_once_with(
            "http://example.com/page", "abc123"
        )
