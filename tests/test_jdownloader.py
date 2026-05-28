"""Tests for hylde.downloaders.jdownloader module."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pyjd.jd_types import AvailableLinkState

from hylde.downloaders import jdownloader


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

        assert result is None
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

        assert result is None
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

        assert result is None
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

        assert result is None
        add_links_query = call_pyjd.call_args.kwargs["add_links_query"]
        assert add_links_query.assignJobID is True
        wait.assert_called_once_with(package_name="pkg", job_id=789)
