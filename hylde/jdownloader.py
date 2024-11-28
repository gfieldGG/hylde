import shutil
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
from hylde.util import md5


JDD: JDDevice


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


def connect() -> JDDevice:
    conn = MyJDConnector()
    connected = conn.connect(
        settings.downloader.jdownloader.email,
        settings.downloader.jdownloader.password,
    )
    if not connected:
        lolg.error("Error while connecting to MyJDownloader API.")
    else:
        lolg.debug("Connected to myJD API.")

    if (
        not settings.downloader.jdownloader.devicename
        or settings.downloader.jdownloader.devicename == "TO BE SET"
    ):
        lolg.info("No MyJDownloader device name configured. Using first device...")
        devices = conn.list_devices()
        device_name = devices[0].name
    else:
        device_name = settings.downloader.jdownloader.devicename

    global JDD
    JDD = conn.get_device(device_name=device_name, refresh_direct_connections=True)
    lolg.success(f"Connected to MyJDownloader device '{JDD.name}'")
    return JDD


def _move_file(target_directory: Path, file_path: Path, folder_name: str = "") -> str:
    file_name = file_path.name
    (target_directory / folder_name).mkdir(exist_ok=True)
    lolg.debug(
        f"Moving '{file_path}' -> '{target_directory / folder_name / file_name}'"
    )
    shutil.move(file_path, target_directory / folder_name / file_name)
    return f"{folder_name}/{file_name}"


def _get_downloader_package(package_name: str) -> FilePackage | None:
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

    package = next(
        (package for package in packages if package.name == package_name), None
    )

    if package:
        lolg.trace(f"Found package with name '{package_name}': {package}")

    return package


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
    package_name: str, interval=5, max_retries=20
) -> int | None:
    lolg.debug(f"Waiting for package '{package_name}' to start downloading...")
    tries = 0
    while tries < max_retries:
        package = _get_downloader_package(package_name)
        if package:
            lolg.debug(f"Found package '{package_name}' in download list.")
            return package.uuid
        else:
            lolg.trace(f"Package '{package_name}' not in download list (yet).")

        lolg.trace(
            f"Looking for '{package_name}' again in {interval}s... ({max_retries-tries} tries left)"
        )
        tries += 1
        time.sleep(interval)

    return None


def _wait_for_package_finish(
    package_name: str, poll_interval=10, max_retries=100
) -> FilePackage | None:
    lolg.debug(f"Waiting for package '{package_name}' to finish downloading...")
    tries = 0
    while tries < max_retries:
        package = _get_downloader_package(package_name)
        if package:
            if package.finished:
                lolg.debug(f"Package '{package_name}' has finished downloading.'")
                return package

            else:
                lolg.debug(
                    f"Package '{package_name}' not finished yet. Status: {package.status}"
                )
        else:
            lolg.error(f"Package '{package_name}' not in download list anymore.")
            return None

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


def _remove_from_downloader(file_name: str, package_id: int):
    link = _get_downloader_link(link_name=file_name, package_id=package_id)
    if not link:
        lolg.error(f"Found no link '{file_name}' in package '{package_id}'.")
        return

    lolg.debug(f"Removing '{link.name}' from downloader...")
    JDD.downloads.cleanup(
        delete_action=DeleteAction.DELETE_ALL,
        mode=Mode.REMOVE_LINKS_AND_DELETE_FILES,
        selection_type=SelectionType.SELECTED,
        link_ids=[link.uuid],
    )


def _get_file(file_name: str, package: FilePackage) -> Path | None:
    package_subpath = Path(package.saveTo).name
    full_path = (
        Path(settings.downloader.jdownloader.outputdir) / package_subpath / file_name
    )
    if not full_path.exists():
        lolg.debug(f"File '{full_path}' not found.")
        return None
    lolg.debug(f"File exists at '{full_path}'")
    return full_path


def download_url(url: str, target_directory: Path) -> list[str] | None:
    """Download file for url. Return final filepaths relative to target_directory."""
    connect()

    package_name = md5(url)
    _call_pyjd(
        JDD.linkgrabber.add_links,
        add_links_query=AddLinksQuery(
            autostart=True,
            autoExtract=False,
            links=url,
            packageName=package_name,
            overwritePackagizerRules=True,
        ),
    )

    lolg.debug(f"Added link '{url}' to package '{package_name}'")

    package_id = _wait_for_package_start(package_name=package_name)
    if not package_id:
        lolg.error(f"Could not add '{url}' to downloader.")
        return None

    package = _wait_for_package_finish(package_name)
    if not package:
        lolg.error(f"Timeout while downloading '{url}' (package '{package_name}')")
        return None

    filenames = _get_filenames_from_package(package.uuid)
    lolg.success(f"Found {len(filenames)} downloaded links for url '{url}'")

    cached_file_names = []
    for fn in filenames:
        f = _get_file(fn, package=package)
        if f:
            cfn = _move_file(
                target_directory=target_directory,
                file_path=f,
                folder_name=package_name,
            )
            cached_file_names.append(cfn)
        else:
            lolg.warning(f"File '{fn}' not found.")
        lolg.info(f"Removing file '{fn}' from downloader...")
        _remove_from_downloader(fn, package_id)

    return cached_file_names
