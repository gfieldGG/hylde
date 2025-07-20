import os
import shutil
import zipfile
from pathlib import Path

from hylde import lolg, settings
from hylde.registry import get_downloader_for_url


cache_directory = Path(settings.cachedir).resolve()


def _zip_files_to_cache(
    target_directory: Path, file_paths: list[Path], folder_name: str = ""
) -> str:
    file_name = f"{folder_name}/{folder_name}.zip"
    output_path = target_directory / file_name
    os.makedirs(output_path.parent, exist_ok=True)
    lolg.debug(f"Zipping {len(file_paths)} files to '{output_path}'...")

    # find the common directory
    common_dir = Path(os.path.commonpath([str(path) for path in file_paths]))
    lolg.debug(f"The common directory is: {common_dir}")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as zipf:
        for file_path in file_paths:
            # add each file to the ZIP, preserving its relative path
            arcname = file_path.relative_to(common_dir.parent)
            zipf.write(file_path, arcname)

    # delete original files
    lolg.debug("Deleting original files...")
    for file_path in file_paths:
        lolg.trace(f"Deleting '{file_path}'...")
        file_path.unlink()
    return file_name


def _move_file_to_cache(
    target_directory: Path, file_path: Path, folder_name: str = ""
) -> str:
    file_name = f"{folder_name}/{file_path.name}"
    output_path = target_directory / file_name
    os.makedirs(output_path.parent, exist_ok=True)
    lolg.debug(f"Moving '{file_path}' -> '{output_path}'")
    shutil.move(file_path, output_path)
    return file_name


def download_file(url: str, url_key: str) -> str | None:
    """
    Return single file path relative to cache directory.
    Return `""` on retryable failure.
    Return `None` on error.
    """

    downloader = get_downloader_for_url(url)
    lolg.debug(f"Using downloader: {downloader.__name__}")
    file_paths = downloader.download_url(url, url_key)

    # file_paths = hyjdl.download_url(url, url_key)

    if file_paths is None:
        lolg.error(f"Error while downloading '{url}'")
        return None
    elif len(file_paths) == 0:
        return ""
    elif len(file_paths) == 1:
        file_name = _move_file_to_cache(cache_directory, file_paths[0], url_key)
    else:
        file_name = _zip_files_to_cache(cache_directory, file_paths, url_key)
    lolg.info(f"Moved file to cache: {file_name}")
    return file_name
