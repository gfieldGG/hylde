"""Tests for hylde.downloaders.jdownloader module."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pyjd.jd_types import AvailableLinkState

from hylde.downloaders import jdownloader
from hylde.result import DownloadError


class TestLinkgrabberOfflineDetection:
    def test_detects_offline_availability_enum(self):
        assert jdownloader._is_offline_availability(AvailableLinkState.OFFLINE)
        assert not jdownloader._is_offline_availability(AvailableLinkState.ONLINE)

    def test_wait_returns_none_and_cleans_linkgrabber_package_when_offline_count(self):
        package = SimpleNamespace(offlineCount=1)

        with (
            patch("hylde.downloaders.jdownloader._get_downloader_packages", return_value={}),
            patch("hylde.downloaders.jdownloader._linkgrabber_job_finished", return_value=True),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: package},
            ),
            patch("hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links") as has_offline_links,
            patch("hylde.downloaders.jdownloader._remove_package_from_linkgrabber") as remove,
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=456, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.message == "File offline."
        assert result.retryable is False
        has_offline_links.assert_not_called()
        remove.assert_called_once_with(123)

    def test_wait_returns_none_and_cleans_linkgrabber_package_when_child_link_offline(self):
        package = SimpleNamespace(offlineCount=0)

        with (
            patch("hylde.downloaders.jdownloader._get_downloader_packages", return_value={}),
            patch("hylde.downloaders.jdownloader._linkgrabber_job_finished", return_value=True),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links",
                return_value=True,
            ),
            patch("hylde.downloaders.jdownloader._remove_package_from_linkgrabber") as remove,
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=456, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.message == "File offline."
        assert result.retryable is False
        remove.assert_called_once_with(123)

    def test_wait_does_not_return_downloader_package_before_linkgrabber_finished(self):
        package = SimpleNamespace(uuid=999)

        with (
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={999: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_job_finished",
                return_value=False,
            ),
            patch("hylde.downloaders.jdownloader._get_linkgrabber_packages") as get_linkgrabber_packages,
            patch("hylde.downloaders.jdownloader.time.sleep"),
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=456, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.retryable is True
        get_linkgrabber_packages.assert_not_called()

    def test_wait_returns_downloader_package_after_linkgrabber_finished(self):
        package = SimpleNamespace(uuid=999)

        with (
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={999: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_job_finished",
                return_value=True,
            ) as job_finished,
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={},
            ),
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=456, interval=0, max_retries=1
            )

        assert result == {999: package}
        job_finished.assert_called_once_with(456)

    def test_wait_moves_online_linkgrabber_packages_to_downloads(self):
        linkgrabber_package = SimpleNamespace(offlineCount=0)

        with (
            patch("hylde.downloaders.jdownloader._get_downloader_packages", return_value={}),
            patch("hylde.downloaders.jdownloader._linkgrabber_job_finished", return_value=True),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: linkgrabber_package, 456: linkgrabber_package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links",
                return_value=False,
            ),
            patch("hylde.downloaders.jdownloader._move_linkgrabber_packages_to_downloader") as move,
            patch("hylde.downloaders.jdownloader.time.sleep"),
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=789, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.retryable is True
        move.assert_called_once_with([123, 456])

    def test_download_url_requests_and_uses_linkgrabber_job_id(self):
        add_links = MagicMock()
        fake_jdd = SimpleNamespace(linkgrabber=SimpleNamespace(add_links=add_links))

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch("hylde.downloaders.jdownloader.connect"),
            patch("hylde.downloaders.jdownloader._get_downloader_packages", return_value={}),
            patch(
                "hylde.downloaders.jdownloader._call_pyjd",
                return_value=SimpleNamespace(id=789),
            ) as call_pyjd,
            patch(
                "hylde.downloaders.jdownloader._wait_for_package_start_or_linkgrabber_failure",
                return_value=None,
            ) as wait,
        ):
            result = jdownloader.download_url("http://example.com/offline", "pkg")

        assert isinstance(result, DownloadError)
        add_links_query = call_pyjd.call_args.kwargs["add_links_query"]
        assert add_links_query.assignJobID is True
        assert add_links_query.autostart is False
        wait.assert_called_once_with(package_name="pkg", job_id=789)

    def test_resolve_finished_packages_reports_missing_link_without_dropping_success(self, tmp_path):
        package = SimpleNamespace(saveTo="/output/pkg")
        good_path = tmp_path / "good.jpg"
        good_path.write_text("ok")
        good_link = SimpleNamespace(
            name="good.jpg",
            enabled=True,
            skipped=None,
            finished=True,
            bytesLoaded=10,
            bytesTotal=10,
            status="Finished",
            url="http://example.com/good",
        )
        missing_link = SimpleNamespace(
            name="missing.jpg",
            enabled=True,
            skipped=None,
            finished=True,
            bytesLoaded=0,
            bytesTotal=10,
            status="File not found",
            url="http://example.com/missing",
        )

        with (
            patch(
                "hylde.downloaders.jdownloader._get_download_links_from_package",
                return_value=[good_link, missing_link],
            ),
            patch(
                "hylde.downloaders.jdownloader._get_full_file_path",
                side_effect=[good_path, None],
            ),
        ):
            paths, failures = jdownloader._resolve_finished_packages({123: package})

        assert paths == [good_path]
        assert len(failures) == 1
        assert "missing.jpg" in failures[0]
        assert "missing file on disk" in failures[0]

    def test_resolve_finished_packages_reports_package_error_even_if_link_resolves(self, tmp_path):
        package = SimpleNamespace(status="An Error occurred!  (bunkr.si)")
        file_path = tmp_path / "file.jpg"
        file_path.write_text("ok")
        link = SimpleNamespace(
            name="file.jpg",
            enabled=True,
            skipped=None,
            finished=True,
            bytesLoaded=10,
            bytesTotal=10,
            status="Finished",
            url="http://example.com/file",
        )

        with (
            patch(
                "hylde.downloaders.jdownloader._get_download_links_from_package",
                return_value=[link],
            ),
            patch("hylde.downloaders.jdownloader._get_full_file_path", return_value=file_path),
        ):
            paths, failures = jdownloader._resolve_finished_packages({123: package})

        assert paths == [file_path]
        assert len(failures) == 1
        assert "An Error occurred!" in failures[0]

    def test_resolve_finished_packages_returns_incomplete_existing_file_for_cleanup(self, tmp_path):
        package = SimpleNamespace(status="Incomplete")
        file_path = tmp_path / "partial.jpg"
        file_path.write_text("partial")
        link = SimpleNamespace(
            name="partial.jpg",
            enabled=True,
            skipped=None,
            finished=False,
            bytesLoaded=5,
            bytesTotal=10,
            status="Downloading",
            url="http://example.com/partial",
        )

        with (
            patch(
                "hylde.downloaders.jdownloader._get_download_links_from_package",
                return_value=[link],
            ),
            patch("hylde.downloaders.jdownloader._get_full_file_path", return_value=file_path),
        ):
            paths, failures = jdownloader._resolve_finished_packages({123: package})

        assert paths == [file_path]
        assert len(failures) == 1
        assert "not marked finished" in failures[0]
        assert "incomplete bytes" in failures[0]

    def test_download_url_deletes_partial_files_and_returns_none_when_finished_job_incomplete(self, tmp_path):
        package = SimpleNamespace(status="An Error occurred!  (bunkr.si)")
        partial_file = tmp_path / "good.jpg"
        partial_file.write_text("partial success")

        with (
            patch("hylde.downloaders.jdownloader.connect"),
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={123: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._wait_for_package_finish",
                return_value={123: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._resolve_finished_packages",
                return_value=([partial_file], ["missing link"]),
            ),
            patch("hylde.downloaders.jdownloader._remove_package_from_downloader") as remove,
        ):
            result = jdownloader.download_url("http://example.com/file", "pkg")

        assert isinstance(result, DownloadError)
        assert result.retryable is False
        assert not partial_file.exists()
        remove.assert_called_once_with(123)
