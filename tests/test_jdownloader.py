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
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_job_finished",
                return_value=True,
            ),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links"
            ) as has_offline_links,
            patch(
                "hylde.downloaders.jdownloader._remove_package_from_linkgrabber"
            ) as remove,
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=456, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.message == "File offline."
        assert result.retryable is False
        has_offline_links.assert_not_called()
        remove.assert_called_once_with(123)

    def test_wait_returns_none_and_cleans_linkgrabber_package_when_child_link_offline(
        self,
    ):
        package = SimpleNamespace(offlineCount=0)

        with (
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_job_finished",
                return_value=True,
            ),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links",
                return_value=True,
            ),
            patch(
                "hylde.downloaders.jdownloader._remove_package_from_linkgrabber"
            ) as remove,
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
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages"
            ) as get_linkgrabber_packages,
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

    def test_wait_moves_online_linkgrabber_packages_without_changing_directory(self):
        linkgrabber_package = SimpleNamespace(offlineCount=0)
        fake_jdd = SimpleNamespace(
            linkgrabber=SimpleNamespace(set_download_directory=MagicMock())
        )

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_job_finished",
                return_value=True,
            ),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: linkgrabber_package, 456: linkgrabber_package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links",
                return_value=False,
            ),
            patch(
                "hylde.downloaders.jdownloader._disable_archive_extraction_for_linkgrabber_packages",
                return_value=None,
            ) as disable_extraction,
            patch(
                "hylde.downloaders.jdownloader._move_linkgrabber_packages_to_downloader"
            ) as move,
            patch("hylde.downloaders.jdownloader.time.sleep"),
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=789, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.retryable is True
        disable_extraction.assert_called_once_with([123, 456])
        move.assert_called_once_with([123, 456])
        fake_jdd.linkgrabber.set_download_directory.assert_not_called()

    def test_download_url_requests_and_uses_linkgrabber_job_id(self):
        add_links = MagicMock()
        fake_jdd = SimpleNamespace(linkgrabber=SimpleNamespace(add_links=add_links))

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch("hylde.downloaders.jdownloader.connect"),
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={},
            ),
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
        assert add_links_query.autoExtract is False
        assert add_links_query.destinationFolder is None
        wait.assert_called_once_with(package_name="pkg", job_id=789)

    def test_get_package_directory_maps_posix_jd_path_to_external_path(self, tmp_path):
        package = SimpleNamespace(saveTo="/output/url-key/album")

        with patch("hylde.downloaders.jdownloader.settings") as settings:
            settings.downloader.jdownloader.outputdir = "/output"
            settings.downloader.jdownloader.externaloutputdir = str(tmp_path)
            result = jdownloader._get_package_directory(package)

        assert result == tmp_path / "url-key" / "album"

    def test_get_package_directory_maps_windows_jd_path_to_external_path(
        self, tmp_path
    ):
        package = SimpleNamespace(saveTo=r"C:\jd-output\url-key\album")

        with patch("hylde.downloaders.jdownloader.settings") as settings:
            settings.downloader.jdownloader.outputdir = r"C:\jd-output"
            settings.downloader.jdownloader.externaloutputdir = str(tmp_path)
            result = jdownloader._get_package_directory(package)

        assert result == tmp_path / "url-key" / "album"

    def test_get_package_directory_rejects_save_to_outside_outputdir(self):
        package = SimpleNamespace(saveTo="/other/url-key/album")

        with patch("hylde.downloaders.jdownloader.settings") as settings:
            settings.downloader.jdownloader.outputdir = "/output"
            settings.downloader.jdownloader.externaloutputdir = "/external"
            try:
                jdownloader._get_package_directory(package)
            except ValueError:
                pass
            else:
                raise AssertionError("Expected ValueError")

    def test_resolve_finished_packages_reports_missing_link_without_dropping_success(
        self, tmp_path
    ):
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

    def test_resolve_finished_packages_dedupes_duplicate_packages_for_same_file(
        self, tmp_path
    ):
        package = SimpleNamespace(saveTo="/output/pkg", status="Finished")
        file_path = tmp_path / "video.mp4"
        file_path.write_text("ok")
        link = SimpleNamespace(
            name="video.mp4",
            enabled=True,
            skipped=None,
            finished=True,
            bytesLoaded=10,
            bytesTotal=10,
            status="Finished",
            url="http://example.com/video",
        )

        with (
            patch(
                "hylde.downloaders.jdownloader._get_download_links_from_package",
                return_value=[link],
            ),
            patch(
                "hylde.downloaders.jdownloader._get_full_file_path",
                return_value=file_path,
            ),
        ):
            paths, failures = jdownloader._resolve_finished_packages(
                {123: package, 456: package}
            )

        assert paths == [file_path]
        assert failures == []

    def test_resolve_finished_packages_keeps_same_name_in_different_dirs(
        self, tmp_path
    ):
        first = tmp_path / "a" / "img.jpg"
        second = tmp_path / "b" / "img.jpg"
        link = SimpleNamespace(
            name="img.jpg",
            enabled=True,
            skipped=None,
            finished=True,
            bytesLoaded=10,
            bytesTotal=10,
            status="Finished",
            url="http://example.com/img",
        )

        with (
            patch(
                "hylde.downloaders.jdownloader._get_download_links_from_package",
                return_value=[link],
            ),
            patch(
                "hylde.downloaders.jdownloader._get_full_file_path",
                side_effect=[first, second],
            ),
        ):
            paths, failures = jdownloader._resolve_finished_packages(
                {123: SimpleNamespace(status=""), 456: SimpleNamespace(status="")}
            )

        assert paths == [first, second]
        assert failures == []

    def test_resolve_finished_packages_ignores_finished_mirror_without_file(
        self, tmp_path
    ):
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
        mirror_link = SimpleNamespace(
            name="duplicate.jpg",
            enabled=True,
            skipped=None,
            finished=True,
            bytesLoaded=0,
            bytesTotal=10,
            status="Finished(Mirror)",
            url="http://example.com/duplicate",
        )

        with (
            patch(
                "hylde.downloaders.jdownloader._get_download_links_from_package",
                return_value=[good_link, mirror_link],
            ),
            patch(
                "hylde.downloaders.jdownloader._get_full_file_path",
                side_effect=[good_path, None],
            ),
        ):
            paths, failures = jdownloader._resolve_finished_packages({123: package})

        assert paths == [good_path]
        assert failures == []

    def test_resolve_finished_packages_reports_package_error_even_if_link_resolves(
        self, tmp_path
    ):
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
            patch(
                "hylde.downloaders.jdownloader._get_full_file_path",
                return_value=file_path,
            ),
        ):
            paths, failures = jdownloader._resolve_finished_packages({123: package})

        assert paths == [file_path]
        assert len(failures) == 1
        assert "An Error occurred!" in failures[0]

    def test_disable_archive_extraction_sets_each_linkgrabber_archive(self):
        fake_jdd = SimpleNamespace(
            connection_helper=SimpleNamespace(action=MagicMock())
        )
        fake_jdd.connection_helper.action.side_effect = [
            [
                {"archiveId": "archive-1"},
                {"archiveId": "archive-2"},
            ],
            True,
            True,
        ]

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_links",
                side_effect=[
                    [SimpleNamespace(uuid=111), SimpleNamespace(uuid=222)],
                    [SimpleNamespace(uuid=333)],
                ],
            ),
        ):
            result = jdownloader._disable_archive_extraction_for_linkgrabber_packages(
                [123, 456]
            )

        assert result is None
        fake_jdd.connection_helper.action.assert_any_call(
            "/extraction/getArchiveInfo", [[111, 222, 333], [123, 456]]
        )
        fake_jdd.connection_helper.action.assert_any_call(
            "/extraction/setArchiveSettings",
            ["archive-1", {"archiveId": "archive-1", "autoExtract": False}],
        )
        fake_jdd.connection_helper.action.assert_any_call(
            "/extraction/setArchiveSettings",
            ["archive-2", {"archiveId": "archive-2", "autoExtract": False}],
        )

    def test_disable_archive_extraction_returns_retryable_error_on_api_failure(self):
        fake_jdd = SimpleNamespace(
            connection_helper=SimpleNamespace(action=MagicMock())
        )
        fake_jdd.connection_helper.action.side_effect = RuntimeError("boom")

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_links",
                return_value=[SimpleNamespace(uuid=111)],
            ),
        ):
            result = jdownloader._disable_archive_extraction_for_linkgrabber_packages(
                [123]
            )

        assert isinstance(result, DownloadError)
        assert result.retryable is True

    def test_disable_archive_extraction_returns_retryable_error_on_link_query_failure(
        self,
    ):
        with patch(
            "hylde.downloaders.jdownloader._get_linkgrabber_links",
            side_effect=RuntimeError("boom"),
        ):
            result = jdownloader._disable_archive_extraction_for_linkgrabber_packages(
                [123]
            )

        assert isinstance(result, DownloadError)
        assert result.retryable is True

    def test_wait_cleans_linkgrabber_packages_when_archive_extraction_disable_fails(
        self,
    ):
        linkgrabber_package = SimpleNamespace(offlineCount=0)

        with (
            patch(
                "hylde.downloaders.jdownloader._get_downloader_packages",
                return_value={},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_job_finished",
                return_value=True,
            ),
            patch(
                "hylde.downloaders.jdownloader._get_linkgrabber_packages",
                return_value={123: linkgrabber_package, 456: linkgrabber_package},
            ),
            patch(
                "hylde.downloaders.jdownloader._linkgrabber_package_has_offline_links",
                return_value=False,
            ),
            patch(
                "hylde.downloaders.jdownloader._disable_archive_extraction_for_linkgrabber_packages",
                return_value=DownloadError("archive settings failed", retryable=True),
            ),
            patch(
                "hylde.downloaders.jdownloader._remove_package_from_linkgrabber"
            ) as remove,
        ):
            result = jdownloader._wait_for_package_start_or_linkgrabber_failure(
                "pkg", job_id=789, interval=0, max_retries=1
            )

        assert isinstance(result, DownloadError)
        assert result.retryable is True
        assert remove.call_count == 2

    def test_resolve_finished_packages_returns_incomplete_existing_file_for_cleanup(
        self, tmp_path
    ):
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
            patch(
                "hylde.downloaders.jdownloader._get_full_file_path",
                return_value=file_path,
            ),
        ):
            paths, failures = jdownloader._resolve_finished_packages({123: package})

        assert paths == [file_path]
        assert len(failures) == 1
        assert "not marked finished" in failures[0]
        assert "incomplete bytes" in failures[0]

    def test_download_url_deletes_partial_files_and_returns_none_when_finished_job_incomplete(
        self, tmp_path
    ):
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
            patch(
                "hylde.downloaders.jdownloader._remove_package_from_downloader"
            ) as remove,
        ):
            result = jdownloader.download_url("http://example.com/file", "pkg")

        assert isinstance(result, DownloadError)
        assert result.retryable is False
        assert not partial_file.exists()
        remove.assert_called_once_with(123)


class TestPackageQueryCap:
    def _fake_jdd(self, packages):
        fake_jdd = MagicMock()
        fake_jdd.downloads.query_packages.return_value = packages
        fake_jdd.linkgrabber.query_packages.return_value = packages
        return fake_jdd

    def test_downloader_query_finds_package_beyond_old_cap_of_100(self):
        packages = [SimpleNamespace(uuid=i, name="other") for i in range(172)]
        packages.append(SimpleNamespace(uuid=999, name="pkg"))
        fake_jdd = self._fake_jdd(packages)

        with patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True):
            result = jdownloader._get_downloader_packages("pkg")

        assert list(result) == [999]
        query = fake_jdd.downloads.query_packages.call_args.kwargs["query_params"]
        assert query.maxResults == jdownloader.PACKAGE_QUERY_MAX_RESULTS

    def test_linkgrabber_query_uses_package_cap(self):
        fake_jdd = self._fake_jdd([SimpleNamespace(uuid=1, name="pkg")])

        with patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True):
            result = jdownloader._get_linkgrabber_packages("pkg")

        assert list(result) == [1]
        query = fake_jdd.linkgrabber.query_packages.call_args.kwargs[
            "crawled_package_query"
        ]
        assert query.maxResults == jdownloader.PACKAGE_QUERY_MAX_RESULTS

    def test_warns_when_query_hits_cap(self):
        packages = [SimpleNamespace(uuid=i, name="other") for i in range(3)]
        fake_jdd = self._fake_jdd(packages)

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch("hylde.downloaders.jdownloader.PACKAGE_QUERY_MAX_RESULTS", 3),
            patch("hylde.downloaders.jdownloader.lolg") as lolg,
        ):
            jdownloader._get_downloader_packages("pkg")

        lolg.warning.assert_called_once()
        assert "Downloads" in lolg.warning.call_args.args[0]

    def test_no_warning_below_cap(self):
        fake_jdd = self._fake_jdd([SimpleNamespace(uuid=1, name="pkg")])

        with (
            patch("hylde.downloaders.jdownloader.JDD", fake_jdd, create=True),
            patch("hylde.downloaders.jdownloader.lolg") as lolg,
        ):
            jdownloader._get_linkgrabber_packages("pkg")

        lolg.warning.assert_not_called()


class TestCallPyjdRetryLogging:
    @staticmethod
    def _flaky(name, failures):
        calls = []

        def func():
            calls.append(1)
            if len(calls) <= failures:
                raise TypeError("'NoneType' object is not a mapping")
            return "ok"

        func.__name__ = func.__qualname__ = name
        return func

    def test_query_retry_logs_debug_and_returns_result(self):
        func = self._flaky("query_packages", failures=1)

        with (
            patch("hylde.downloaders.jdownloader.time.sleep"),
            patch("hylde.downloaders.jdownloader.lolg") as lolg,
        ):
            assert jdownloader._call_pyjd(func) == "ok"

        lolg.debug.assert_called_once()
        assert "query_packages" in lolg.debug.call_args.args[0]
        lolg.warning.assert_not_called()

    def test_mutating_retry_logs_warning(self):
        func = self._flaky("add_links", failures=1)

        with (
            patch("hylde.downloaders.jdownloader.time.sleep"),
            patch("hylde.downloaders.jdownloader.lolg") as lolg,
        ):
            assert jdownloader._call_pyjd(func) == "ok"

        lolg.warning.assert_called_once()
        assert "add_links" in lolg.warning.call_args.args[0]

    def test_exhausted_retries_log_error_with_call_name(self):
        func = self._flaky("cleanup", failures=3)

        with (
            patch("hylde.downloaders.jdownloader.time.sleep"),
            patch("hylde.downloaders.jdownloader.lolg") as lolg,
        ):
            try:
                jdownloader._call_pyjd(func)
            except RuntimeError as e:
                assert str(e) == "pyjd call failed"
            else:
                raise AssertionError("Expected RuntimeError")

        assert lolg.warning.call_count == 3
        assert "cleanup" in lolg.error.call_args.args[0]
