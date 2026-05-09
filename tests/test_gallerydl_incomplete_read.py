"""Tests for gallery-dl IncompleteRead detection and retry handling."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gallery_dl as gdl
from hylde.downloaders.gallerydl import (
    _IncompleteReadAdapter,
    download_url,
)


class TestIncompleteReadAdapter:
    """Tests for _IncompleteReadAdapter."""

    @pytest.fixture
    def job(self):
        """Return a mock job object with a has_incomplete_read flag."""
        j = MagicMock()
        j.has_incomplete_read = False
        j._logger_extra = {}
        return j

    @pytest.fixture
    def adapter(self, job):
        """Return an adapter wrapping a stdlib logger."""
        import logging

        return _IncompleteReadAdapter(logging.getLogger("test"), job)

    def test_detects_in_message(self, adapter, job):
        adapter.warning(
            "Connection broken: IncompleteRead(123 bytes read, 456 more expected)"
        )
        assert job.has_incomplete_read is True

    def test_detects_in_args(self, adapter, job):
        adapter.warning("%s", "IncompleteRead(789 bytes read)")
        assert job.has_incomplete_read is True

    def test_detects_in_error_level(self, adapter, job):
        adapter.error("Failed: IncompleteRead(0 bytes read)")
        assert job.has_incomplete_read is True

    def test_ignores_unrelated_warning(self, adapter, job):
        adapter.warning("Some other network error")
        assert job.has_incomplete_read is False

    def test_ignores_unrelated_error(self, adapter, job):
        adapter.error("404 Not Found")
        assert job.has_incomplete_read is False

    def test_detects_in_formatted_message(self, adapter, job):
        adapter.warning("Error: %s", "IncompleteRead(100 bytes)")
        assert job.has_incomplete_read is True


class TestDownloadUrl:
    """Tests for download_url IncompleteRead handling."""

    @pytest.fixture
    def fake_job(self):
        """Return a fake GoodJob-like object."""
        job = MagicMock()
        job.has_incomplete_read = False
        return job

    @pytest.fixture
    def fake_collector(self, tmp_path):
        """Return a fake FileCollector with a real temp file."""
        temp_file = tmp_path / "partial" / "file.mp4"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_text("partial data")

        fc = MagicMock()
        fc.files = [temp_file]
        fc.errors = []
        return fc

    @pytest.fixture(autouse=True)
    def patch_gdl_config(self):
        """Prevent gallery-dl config from mutating global state."""
        with patch.object(gdl.config, "set"):
            yield

    def test_returns_empty_list_on_incomplete_read(self, fake_job, fake_collector):
        fake_job.has_incomplete_read = True

        with patch(
            "hylde.downloaders.gallerydl.GoodJob", return_value=fake_job
        ), patch(
            "hylde.downloaders.gallerydl.FileCollector", return_value=fake_collector
        ):
            result = download_url("https://example.com/file", "key")

        assert result == []

    def test_deletes_temp_files_on_incomplete_read(self, fake_job, fake_collector):
        temp_file = fake_collector.files[0]
        assert temp_file.exists()
        fake_job.has_incomplete_read = True

        with patch(
            "hylde.downloaders.gallerydl.GoodJob", return_value=fake_job
        ), patch(
            "hylde.downloaders.gallerydl.FileCollector", return_value=fake_collector
        ):
            download_url("https://example.com/file", "key")

        assert not temp_file.exists()

    def test_returns_none_on_regular_errors(self, fake_job, fake_collector):
        fake_job.has_incomplete_read = False
        fake_collector.errors = [Path("/tmp/error.txt")]

        with patch(
            "hylde.downloaders.gallerydl.GoodJob", return_value=fake_job
        ), patch(
            "hylde.downloaders.gallerydl.FileCollector", return_value=fake_collector
        ):
            result = download_url("https://example.com/file", "key")

        assert result is None

    def test_returns_files_on_success(self, fake_job, fake_collector):
        fake_job.has_incomplete_read = False
        fake_collector.errors = []

        with patch(
            "hylde.downloaders.gallerydl.GoodJob", return_value=fake_job
        ), patch(
            "hylde.downloaders.gallerydl.FileCollector", return_value=fake_collector
        ):
            result = download_url("https://example.com/file", "key")

        assert result == fake_collector.files
