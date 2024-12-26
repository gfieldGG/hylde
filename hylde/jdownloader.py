import time
from pathlib import Path

from pyjd.myjd_connector import MyJDConnector, JDDevice  # type:ignore
from pyjd.jd_types import (  # type:ignore
    AddLinksQuery,
    PackageQuery,
    FilePackage,
    LinkQuery,
    DownloadLink,
    DeleteAction,
    Mode,
    SelectionType,
)

from hylde import lolg, settings


JDD: JDDevice

if (
    settings.downloader.jdownloader.email == "TO BE SET"
    or settings.downloader.jdownloader.password == "TO BE SET"
):
    raise ValueError("MyJDownloader API credentials not set.")


def _call_pyjd(func, retries=3, delay=1, *args, **kwargs):
    """Wrap pyjd calls in retries because this is so nice to work with."""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except TypeError as e:
            lolg.trace(f"Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:  # Don't wait after the last attempt
                time.sleep(delay)
    lolg.error(f"pyjd call failed after {retries} attempts")
    raise RuntimeError("pyjd call failed")


def connect() -> JDDevice | None:
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


def _get_downloader_packages(package_name: str) -> dict[int, FilePackage] | None:
    packages = _call_pyjd(
        JDD.downloads.query_packages,
        query_params=PackageQuery(
            status=True,
            finished=True,
            enabled=True,
            saveTo=True,
            maxResults=100,
        ),
    )

    packages = {
        package.uuid: package for package in packages if package.name == package_name
    }

    if packages:
        lolg.trace(f"Found {len(packages)} packages with name '{package_name}'")

    return packages


def _get_downloader_link(link_name: str, package_id: int) -> DownloadLink | None:
    links = _call_pyjd(
        JDD.downloads.query_links,
        query_params=LinkQuery(
            packageUUIDs=[package_id],
            status=True,
            url=True,
            finished=True,
            enabled=True,
            maxResults=1000,
        ),
    )
    link = next((link for link in links if link.name == link_name), None)
    if link:
        lolg.trace(f"Found link '{link_name}' in '{package_id}': {link}")
    return link


def _wait_for_package_start(
    package_name: str, interval=5, max_retries=24
) -> dict[int, FilePackage] | None:
    lolg.debug(f"Waiting for package '{package_name}' to start downloading...")
    tries = 0
    while tries < max_retries:
        packages = _get_downloader_packages(package_name)
        if packages:
            lolg.debug(f"Found package '{package_name}' in download list.")
            return packages
        else:
            lolg.trace(f"Package '{package_name}' not in download list (yet).")

        lolg.trace(
            f"Looking for '{package_name}' again in {interval}s... ({max_retries-tries} tries left)"
        )
        tries += 1
        time.sleep(interval)

    return None


def _wait_for_package_finish(
    package_name: str, poll_interval=5, max_retries=120
) -> dict[int, FilePackage] | None:
    lolg.debug(f"Waiting for package '{package_name}' to finish downloading...")
    tries = 0
    while tries < max_retries:
        packages = _get_downloader_packages(package_name)

        if not packages:
            lolg.error(f"Package '{package_name}' not in download list anymore.")
            return None

        all_finished = True
        for package_id, package in packages.items():
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
            f"Checking status of '{package_name}' again in {poll_interval}s... ({max_retries-tries} tries left)"
        )
        tries += 1
        time.sleep(poll_interval)

    return None


def _get_filenames_from_package(package_id: int):
    links = _call_pyjd(
        JDD.downloads.query_links,
        query_params=LinkQuery(
            packageUUIDs=[package_id],
            status=True,
            url=True,
            finished=True,
            enabled=True,
            maxResults=1000,
        ),
    )
    lolg.debug(f"Found {len(links)} links in package '{package_id}'")
    filenames = [link.name for link in links]
    return filenames


def _remove_package_from_downloader(package_id: int):
    lolg.debug(f"Removing package id '{package_id}' from downloader...")
    _call_pyjd(
        JDD.downloads.cleanup,
        delete_action=DeleteAction.DELETE_ALL,
        mode=Mode.REMOVE_LINKS_ONLY,
        selection_type=SelectionType.SELECTED,
        package_ids=[package_id],
    )


def _get_full_file_path(file_name: str, package: FilePackage) -> Path | None:
    package_subpath = Path(package.saveTo).relative_to(
        settings.downloader.jdownloader.outputdir
    )
    lolg.trace(f"Calculated package subpath: {package_subpath}")
    full_path = (
        Path(settings.downloader.jdownloader.externaloutputdir)
        / package_subpath
        / file_name
    )
    if not full_path.exists():
        lolg.debug(f"File '{full_path}' not found.")
        return None
    lolg.trace(f"File exists at '{full_path}'")
    return full_path


def download_url(url: str, url_key: str) -> list[Path] | None:
    """Download file for url. Return full file paths. Return empty list on (recoverable) problems. Return None if download failed."""
    connect()

    package_name = url_key

    # don't add package again if already/still in download list
    if not _get_downloader_packages(package_name):
        # add link to linkgrabber
        _call_pyjd(
            JDD.linkgrabber.add_links,
            add_links_query=AddLinksQuery(
                autostart=True,
                autoExtract=False,
                links=url,
                packageName=package_name,
                overwritePackagizerRules=True,  # need fixed package name
            ),
        )
        lolg.debug(f"Added link '{url}' to package '{package_name}'")

        packages = _wait_for_package_start(package_name=package_name)
        if not packages:
            lolg.debug(packages)
            lolg.error(f"Could not add '{url_key}' to downloader.")
            return None
    else:
        lolg.debug(f"Package '{package_name}' already in download list.")

    packages = _wait_for_package_finish(package_name)
    if not packages:
        lolg.debug(packages)
        lolg.warning(f"Timeout while waiting for '{url_key}' to finish.")
        return []

    full_file_paths: list[Path] = []
    for package_id, package in packages.items():
        if "An Error occurred!" in package.status:
            lolg.error(f"Error in package '{package_id}': {package.status}")
            full_file_paths = None  # type:ignore
            break

        filenames = _get_filenames_from_package(package_id)
        # resolve filenames to full paths
        for fn in filenames:
            f = _get_full_file_path(fn, package=package)
            if f:
                lolg.trace(f"Found full file path '{f}'")
                full_file_paths.append(f)
            else:
                lolg.warning(f"File '{fn}' not found.")

    if full_file_paths:
        lolg.success(
            f"Found {len(full_file_paths)} downloaded files for url '{url_key}'"
        )

    # clean up packages
    lolg.info(f"Removing package '{package_name}' from downloader...")
    for package_id in packages:
        _remove_package_from_downloader(package_id)

    return full_file_paths
