import time
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from pyjd.jd_types import (  # type:ignore
    AddLinksQuery,
    AvailableLinkState,
    CrawledLinkQuery,
    CrawledPackageQuery,
    DeleteAction,
    FilePackage,
    LinkCrawlerJobsQuery,
    LinkQuery,
    Mode,
    PackageQuery,
    SelectionType,
)
from pyjd.myjd_connector import JDDevice, MyJDConnector  # type:ignore

from hylde import lolg, settings
from hylde.result import DownloaderResult, DownloadError

JDD: JDDevice

ERROR_MESSAGES = ("An Error occurred!", "File not found")

# JD returns packages oldest first, so a too-low cap hides newly added packages.
PACKAGE_QUERY_MAX_RESULTS = 1000

# pyjd calls that change JD state; retrying them may repeat the action.
MUTATING_PYJD_CALLS = ("add_links", "cleanup", "move_to_downloadlist")


if (
    settings.downloader.jdownloader.email == "TO BE SET"
    or settings.downloader.jdownloader.password == "TO BE SET"
):
    raise ValueError("MyJDownloader API credentials not set.")


def _call_pyjd(func, retries=3, delay=1, *args, **kwargs):
    """Wrap pyjd calls in retries because this is so nice to work with."""
    name = getattr(func, "__qualname__", repr(func))
    # pyjd returns None on request errors/timeouts, which surfaces as TypeError
    log = (
        lolg.warning
        if getattr(func, "__name__", "") in MUTATING_PYJD_CALLS
        else lolg.debug
    )
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except TypeError as e:
            log(f"pyjd call '{name}' attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:  # Don't wait after the last attempt
                time.sleep(delay)
    lolg.error(f"pyjd call '{name}' failed after {retries} attempts")
    raise RuntimeError("pyjd call failed")


def connect() -> JDDevice | None:
    """Connect to the configured MyJDownloader device and cache it globally."""
    conn = MyJDConnector()

    lolg.debug("Trying to connect to MyJDownloader API...")
    connected = conn.connect(
        settings.downloader.jdownloader.email,
        settings.downloader.jdownloader.password,
    )
    if not connected:
        lolg.error("Error while connecting to MyJDownloader API.")
        raise RuntimeError("Could not connect to MyJDownloader API.")
    else:
        lolg.debug("Connected to MyJDownloader API.")

    if (
        not settings.downloader.jdownloader.devicename
        or settings.downloader.jdownloader.devicename == "TO BE SET"
    ):
        lolg.info("No device name configured. Using first device...")
        devices = conn.list_devices()
        device_name = devices[0].get("name")
    else:
        device_name = settings.downloader.jdownloader.devicename

    global JDD
    JDD = conn.get_device(device_name=device_name, refresh_direct_connections=True)
    lolg.debug(f"Connected to MyJDownloader device '{JDD.name}'")
    return JDD


def _warn_if_package_query_truncated(packages: list, list_name: str):
    """Warn when a package query hit the result cap and may be missing packages."""
    if len(packages) >= PACKAGE_QUERY_MAX_RESULTS:
        lolg.warning(
            f"JDownloader {list_name} query returned {len(packages)} packages "
            f"(cap {PACKAGE_QUERY_MAX_RESULTS}); newer packages may be invisible."
        )


def _get_downloader_packages(package_name: str) -> dict[int, FilePackage] | None:
    """Return Downloads-list packages matching Hylde's fixed package name."""
    packages = _call_pyjd(
        JDD.downloads.query_packages,
        query_params=PackageQuery(
            status=True,
            finished=True,
            enabled=True,
            saveTo=True,
            maxResults=PACKAGE_QUERY_MAX_RESULTS,
        ),
    )
    _warn_if_package_query_truncated(packages, "Downloads")

    packages = {
        package.uuid: package for package in packages if package.name == package_name
    }

    if packages:
        lolg.trace(f"Found {len(packages)} packages with name '{package_name}'")

    return packages


def _get_linkgrabber_packages(package_name: str):
    """Return LinkGrabber/collector packages matching Hylde's package name."""
    packages = _call_pyjd(
        JDD.linkgrabber.query_packages,
        crawled_package_query=CrawledPackageQuery(
            availableOfflineCount=True,
            availableOnlineCount=True,
            availableTempUnknownCount=True,
            availableUnknownCount=True,
            childCount=True,
            saveTo=True,
            status=True,
            maxResults=PACKAGE_QUERY_MAX_RESULTS,
        ),
    )
    _warn_if_package_query_truncated(packages, "LinkGrabber")
    packages = {
        package.uuid: package for package in packages if package.name == package_name
    }

    if packages:
        lolg.trace(f"Found {len(packages)} LinkGrabber packages named '{package_name}'")

    return packages


def _get_linkgrabber_links(package_id: int):
    """Return LinkGrabber child links for one collector package."""
    links = _call_pyjd(
        JDD.linkgrabber.query_links,
        crawled_link_query=CrawledLinkQuery(
            packageUUIDs=[package_id],
            availability=True,
            status=True,
            url=True,
            enabled=True,
            maxResults=1000,
        ),
    )
    lolg.trace(f"Found {len(links)} LinkGrabber links in package '{package_id}'")
    return links


def _linkgrabber_job_finished(job_id: int) -> bool:
    """Return whether JDownloader has finished crawling/checking added links."""
    jobs = _call_pyjd(
        JDD.linkgrabber.query_link_crawler_jobs,
        link_crawler_jobs_query=LinkCrawlerJobsQuery(
            collectorInfo=True,
            jobIds=[job_id],
        ),
    )
    if not jobs:
        lolg.trace(f"LinkGrabber crawler job '{job_id}' not found; assuming finished.")
        return True

    for job in jobs:
        if job.crawling or job.checking:
            lolg.trace(f"LinkGrabber crawler job '{job_id}' is still active: {job}")
            return False

    if _call_pyjd(JDD.linkgrabber.is_collecting):
        lolg.trace("LinkGrabber is still collecting links.")
        return False

    lolg.trace(f"LinkGrabber crawler job '{job_id}' has finished.")
    return True


def _is_offline_availability(availability) -> bool:
    """Return True when a pyjd availability value means the link is offline."""
    if availability == AvailableLinkState.OFFLINE:
        return True
    availability_value = getattr(availability, "value", availability)
    return str(availability_value).upper().endswith("OFFLINE")


def _linkgrabber_package_has_offline_links(package_id: int) -> bool:
    """Return True if any child link in a collector package is offline."""
    links = _get_linkgrabber_links(package_id)
    for link in links:
        if _is_offline_availability(link.availability):
            lolg.error(
                f"Offline LinkGrabber link in package '{package_id}': "
                f"{link.name} ({link.url})"
            )
            return True
    return False


def _remove_package_from_linkgrabber(package_id: int):
    """Remove a failed collector package so it does not linger in LinkGrabber."""
    lolg.debug(f"Removing package id '{package_id}' from LinkGrabber...")
    _call_pyjd(
        JDD.linkgrabber.cleanup,
        delete_action=DeleteAction.DELETE_ALL,
        mode=Mode.REMOVE_LINKS_ONLY,
        selection_type=SelectionType.SELECTED,
        package_ids=[package_id],
    )


def _move_linkgrabber_packages_to_downloader(package_ids: list[int]):
    """Move online LinkGrabber packages to the Downloads list immediately."""
    lolg.debug(f"Moving LinkGrabber package ids to downloader: {package_ids}")
    _call_pyjd(
        JDD.linkgrabber.move_to_downloadlist,
        link_ids=[],
        package_ids=package_ids,
    )


def _jd_path_class() -> type[PurePath]:
    """Return the PurePath class matching JDownloader's configured paths."""
    output_dir = str(settings.downloader.jdownloader.outputdir)
    if "\\" in output_dir or ":" in output_dir:
        return PureWindowsPath
    return PurePosixPath


def _jd_output_dir() -> PurePath:
    """Return JDownloader's configured output directory as a JD-side path."""
    return _jd_path_class()(str(settings.downloader.jdownloader.outputdir))


def _jd_relative_to_output(path: object) -> PurePath:
    """Return a JD-side path relative to JDownloader's output directory."""
    jd_path = _jd_path_class()
    return jd_path(str(path)).relative_to(_jd_output_dir())


def _external_path_for_jd_save_to(save_to: object) -> Path:
    """Map a JD-side save path to Hylde's mounted filesystem path."""
    relative_subpath = _jd_relative_to_output(save_to)
    return Path(settings.downloader.jdownloader.externaloutputdir).joinpath(
        *relative_subpath.parts
    )


def _disable_archive_extraction_for_linkgrabber_packages(
    package_ids: list[int],
) -> DownloadError | None:
    """Disable extraction for archives recognized in LinkGrabber packages."""
    try:
        link_ids = [
            link.uuid
            for package_id in package_ids
            for link in _get_linkgrabber_links(package_id)
            if getattr(link, "uuid", None) is not None
        ]
    except Exception as e:  # noqa: BLE001
        lolg.error(f"Could not query JDownloader LinkGrabber links: {e}")
        return DownloadError("JDownloader archive settings failed.", retryable=True)
    if not link_ids:
        return DownloadError(
            "JDownloader LinkGrabber package has no links.", retryable=True
        )

    try:
        archive_info = JDD.connection_helper.action(
            "/extraction/getArchiveInfo", [link_ids, package_ids]
        )
    except Exception as e:  # noqa: BLE001
        lolg.error(f"Could not query JDownloader archive info: {e}")
        return DownloadError("JDownloader archive settings failed.", retryable=True)

    archive_ids = [
        archive.get("archiveId")
        for archive in archive_info or []
        if archive.get("archiveId")
    ]
    if not archive_ids:
        lolg.debug("No JDownloader archives found in LinkGrabber packages.")
        return None

    lolg.debug(f"Disabling extraction for {len(archive_ids)} JDownloader archive(s).")
    for archive_id in archive_ids:
        try:
            result = JDD.connection_helper.action(
                "/extraction/setArchiveSettings",
                [archive_id, {"archiveId": archive_id, "autoExtract": False}],
            )
        except Exception as e:  # noqa: BLE001
            lolg.error(f"Could not disable extraction for archive '{archive_id}': {e}")
            return DownloadError("JDownloader archive settings failed.", retryable=True)

        if result is not True:
            lolg.error(
                f"JDownloader returned {result!r} while disabling extraction for "
                f"archive '{archive_id}'."
            )
            return DownloadError("JDownloader archive settings failed.", retryable=True)

    return None


def _wait_for_package_start_or_linkgrabber_failure(
    package_name: str, job_id: int, interval=2, max_retries=60
) -> dict[int, FilePackage] | DownloadError:
    """Wait until LinkGrabber settles, then return Downloads packages or a failure."""
    lolg.debug(
        f"Waiting for package '{package_name}' to start downloading or fail in LinkGrabber..."
    )
    tries = 0
    moved_linkgrabber_package_ids: set[int] = set()
    while tries < max_retries:
        packages = _get_downloader_packages(package_name)
        linkgrabber_finished = _linkgrabber_job_finished(job_id)

        if linkgrabber_finished:
            linkgrabber_packages = _get_linkgrabber_packages(package_name)

            if linkgrabber_packages:
                for package_id, package in linkgrabber_packages.items():
                    offline_count = getattr(package, "offlineCount", None)
                    if offline_count is None:
                        offline_count = getattr(package, "availableOfflineCount", 0)

                    if offline_count or _linkgrabber_package_has_offline_links(
                        package_id
                    ):
                        lolg.error(
                            f"Package '{package_name}' failed in LinkGrabber: "
                            f"{offline_count} offline link(s)."
                        )
                        for failed_package_id in linkgrabber_packages:
                            _remove_package_from_linkgrabber(failed_package_id)
                        return DownloadError("File offline.", retryable=False)

                package_ids_to_move = [
                    package_id
                    for package_id in linkgrabber_packages
                    if package_id not in moved_linkgrabber_package_ids
                ]
                if package_ids_to_move:
                    extraction_error = (
                        _disable_archive_extraction_for_linkgrabber_packages(
                            package_ids_to_move
                        )
                    )
                    if extraction_error:
                        for failed_package_id in linkgrabber_packages:
                            _remove_package_from_linkgrabber(failed_package_id)
                        return extraction_error

                    _move_linkgrabber_packages_to_downloader(package_ids_to_move)
                    moved_linkgrabber_package_ids.update(package_ids_to_move)

            if packages:
                lolg.debug(f"Found package '{package_name}' in download list.")
                return packages

        elif packages:
            lolg.trace(
                f"Package '{package_name}' is in the download list, but LinkGrabber is still checking it."
            )

        lolg.trace(
            f"Looking for '{package_name}' again in {interval}s... ({max_retries - tries} tries left)"
        )
        tries += 1
        time.sleep(interval)

    return DownloadError("JDownloader collector timeout.", retryable=True)


def _wait_for_package_finish(
    package_name: str, poll_interval=5, max_retries=120
) -> dict[int, FilePackage] | DownloadError:
    """Poll the Downloads list until all matching packages are finished."""
    lolg.debug(f"Waiting for package '{package_name}' to finish downloading...")
    tries = 0
    while tries < max_retries:
        packages = _get_downloader_packages(package_name)

        if not packages:
            lolg.error(f"Package '{package_name}' not in download list anymore.")
            return DownloadError("JDownloader download gone.", retryable=True)

        all_finished = True
        for package in packages.values():
            if not package.finished:
                lolg.trace(
                    f"Package '{package_name}' not finished yet. Status: {package.status}"
                )
                all_finished = False
                break

        if all_finished:
            lolg.debug(f"Packages '{package_name}' have finished downloading.'")
            return packages

        lolg.trace(
            f"Checking status of '{package_name}' again in {poll_interval}s... ({max_retries - tries} tries left)"
        )
        tries += 1
        time.sleep(poll_interval)

    return DownloadError("JDownloader timeout.", retryable=True)


def _get_download_links_from_package(package_id: int):
    """Return JDownloader download links for one Downloads-list package."""
    links = _call_pyjd(
        JDD.downloads.query_links,
        query_params=LinkQuery(
            packageUUIDs=[package_id],
            bytesLoaded=True,
            bytesTotal=True,
            enabled=True,
            finished=True,
            host=True,
            skipped=True,
            status=True,
            url=True,
            maxResults=1000,
        ),
    )
    lolg.debug(f"Found {len(links)} links in package '{package_id}'")
    return links


def _get_filenames_from_package(package_id: int):
    """Return JDownloader link names, which correspond to downloaded filenames."""
    links = _get_download_links_from_package(package_id)
    filenames = [link.name for link in links]
    return filenames


def _remove_package_from_downloader(package_id: int):
    """Remove a completed package from JDownloader's Downloads list."""
    lolg.debug(f"Removing package id '{package_id}' from downloader...")
    _call_pyjd(
        JDD.downloads.cleanup,
        delete_action=DeleteAction.DELETE_ALL,
        mode=Mode.REMOVE_LINKS_ONLY,
        selection_type=SelectionType.SELECTED,
        package_ids=[package_id],
    )


def _get_package_directory(package: FilePackage) -> Path:
    """Map JDownloader's internal package save path to Hylde's mounted filesystem path."""
    package_dir = _external_path_for_jd_save_to(package.saveTo)
    lolg.trace(f"Calculated package directory: {package_dir}")
    return package_dir


def _get_full_file_path(file_name: str, package: FilePackage) -> Path | None:
    """Map JDownloader's internal save path to Hylde's mounted filesystem path."""
    full_path = _get_package_directory(package) / file_name
    if not full_path.exists():
        lolg.debug(f"File '{full_path}' not found.")
        return None
    lolg.trace(f"File exists at '{full_path}'")
    return full_path


def _is_finished_mirror_link(link) -> bool:
    """Return True when JDownloader reports a satisfied mirror duplicate."""
    status = str(getattr(link, "status", "") or "")
    return link.finished is True and status.casefold() == "finished(mirror)"


def _link_failure_reasons(link, file_path: Path | None) -> list[str]:
    """Return reasons a finished JDownloader link is not a usable downloaded file."""
    if _is_finished_mirror_link(link):
        return []

    reasons = []

    if link.enabled is False:
        reasons.append("disabled")

    if getattr(link, "skipped", None):
        reasons.append("skipped")

    if link.finished is not True:
        reasons.append("not marked finished")

    bytes_loaded = getattr(link, "bytesLoaded", None)
    bytes_total = getattr(link, "bytesTotal", None)
    if (
        bytes_loaded is not None
        and bytes_total is not None
        and bytes_total > 0
        and bytes_loaded < bytes_total
    ):
        reasons.append(f"incomplete bytes ({bytes_loaded}/{bytes_total})")

    if file_path is None:
        reasons.append("missing file on disk")

    return reasons


def _delete_partial_files(file_paths: list[Path]):
    """Delete downloaded files from a failed all-or-nothing JDownloader job."""
    for file_path in set(file_paths):
        if file_path.exists():
            lolg.debug(f"Deleting partial downloaded file '{file_path}'...")
            file_path.unlink()


def _resolve_finished_packages(
    packages: dict[int, FilePackage],
) -> tuple[list[Path], list[str]]:
    """Return resolved file paths and failure reasons for finished JDownloader packages."""
    full_file_paths: list[Path] = []
    failures: list[str] = []

    for package_id, package in packages.items():
        package_status = getattr(package, "status", "") or ""
        if any(error in package_status for error in ERROR_MESSAGES):
            failures.append(f"package '{package_id}' failed: status={package_status!r}")

        links = _get_download_links_from_package(package_id)
        if not links:
            failures.append(f"package '{package_id}' has no download links")
            continue

        for link in links:
            file_path = _get_full_file_path(link.name, package=package)
            reasons = _link_failure_reasons(link, file_path)

            # duplicate packages or mirror links can resolve to the same file
            if file_path and file_path not in full_file_paths:
                lolg.trace(f"Found full file path '{file_path}'")
                full_file_paths.append(file_path)

            if reasons:
                failures.append(
                    f"link '{link.name}' in package '{package_id}' failed: "
                    f"{', '.join(reasons)}; status={link.status!r}; url={link.url!r}"
                )

    if not full_file_paths and not failures:
        failures.append("finished JDownloader job produced no files")

    return full_file_paths, failures


def download_url(url: str, url_key: str) -> DownloaderResult:
    """Download URL via JDownloader and return paths or a user-facing error."""
    try:
        connect()
    except Exception:  # noqa: BLE001
        return DownloadError("JDownloader connection failed.", retryable=True)

    package_name = url_key

    # don't add package again if already/still in download list
    if not _get_downloader_packages(package_name):
        # add link to linkgrabber
        job = _call_pyjd(
            JDD.linkgrabber.add_links,
            add_links_query=AddLinksQuery(
                assignJobID=True,
                autostart=False,
                autoExtract=False,
                links=url,
                packageName=package_name,
                overwritePackagizerRules=True,  # need fixed package name
            ),
        )
        lolg.debug(f"Added link '{url}' to package '{package_name}'")

        packages = _wait_for_package_start_or_linkgrabber_failure(
            package_name=package_name,
            job_id=job.id,
        )
        if isinstance(packages, DownloadError):
            lolg.error(f"Could not add '{url_key}' to downloader: {packages.message}")
            return packages
    else:
        lolg.debug(f"Package '{package_name}' already in download list.")

    packages = _wait_for_package_finish(package_name)
    if isinstance(packages, DownloadError):
        lolg.warning(
            f"JDownloader failed while waiting for '{url_key}': {packages.message}"
        )
        return packages

    full_file_paths, failures = _resolve_finished_packages(packages)

    # clean up packages
    lolg.info(f"Removing package '{package_name}' from downloader...")
    for package_id in packages:
        _remove_package_from_downloader(package_id)

    if failures:
        for failure in failures:
            lolg.error(failure)
        _delete_partial_files(full_file_paths)
        return DownloadError("JDownloader failed.", retryable=False)

    lolg.success(f"Found {len(full_file_paths)} downloaded files for url '{url_key}'")
    return full_file_paths
