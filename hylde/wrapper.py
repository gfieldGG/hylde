import os
import shutil
import zipfile
from pathlib import Path

from hylde import lolg, settings
from hylde import multipart
from hylde.registry import get_downloader_for_url
from hylde.result import (
    DownloadError,
    MultipartAccepted,
    MultipartCompleted,
    WrapperResult,
)


def _cache_dir() -> Path:
    return Path(settings.cachedir).resolve()


def _zip_files_to_cache(
    target_directory: Path, file_paths: list[Path], folder_name: str = ""
) -> str:
    if not file_paths:
        raise ValueError("Cannot zip an empty list of files.")

    file_name = f"{folder_name}/{folder_name}.zip"
    output_path = target_directory / file_name
    lolg.debug(f"Creating cache folder: {output_path.parent}")
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
    dst_file_name = f"{folder_name}/{file_path.name}"
    output_path = target_directory / dst_file_name
    lolg.debug(f"Creating cache folder: {output_path.parent}")
    os.makedirs(output_path.parent, exist_ok=True)
    lolg.debug(f"Moving '{file_path}' -> '{output_path}'")
    shutil.move(file_path, output_path)
    return dst_file_name


def download_file(url: str, url_key: str) -> WrapperResult:
    """Return cache-relative file path, or a user-facing download error."""

    downloader = get_downloader_for_url(url)
    lolg.debug(f"Using downloader: {downloader.__name__}")

    try:
        result = downloader.download_url(url, url_key)
    except Exception as e:
        lolg.error(f"Unhandled error while downloading '{url}': {e}")
        return DownloadError(str(e), retryable=True)

    if isinstance(result, DownloadError):
        lolg.error(f"Error while downloading '{url}': {result.message}")
        return result

    if not result:
        return DownloadError("Downloader returned no files.", retryable=False)

    if len(result) == 1:
        multipart_result = multipart.process_downloaded_file(url, url_key, result[0])
        if isinstance(multipart_result, DownloadError | MultipartAccepted):
            return multipart_result
        if isinstance(multipart_result, multipart.MultipartAlreadyComplete):
            lolg.info(
                f"Multipart archive '{multipart_result.group}' already completed: "
                f"{multipart_result.final_cache_path}"
            )
            return multipart_result.final_cache_path
        if isinstance(multipart_result, multipart.MultipartExtractionReady):
            try:
                if len(multipart_result.files) == 1:
                    file_name = _move_file_to_cache(
                        _cache_dir(), multipart_result.files[0], url_key
                    )
                else:
                    file_name = _zip_files_to_cache(
                        _cache_dir(), multipart_result.files, url_key
                    )
            except Exception:
                multipart.clear_finalizing(multipart_result.group)
                shutil.rmtree(_cache_dir() / url_key, ignore_errors=True)
                raise
            url_keys = multipart.complete_group(multipart_result.group, file_name)
            lolg.info(f"Moved multipart archive output to cache: {file_name}")
            return MultipartCompleted(file_name=file_name, url_keys=url_keys)

    if len(result) == 1:
        file_name = _move_file_to_cache(_cache_dir(), result[0], url_key)
    else:
        file_name = _zip_files_to_cache(_cache_dir(), result, url_key)
    lolg.info(f"Moved file to cache: {file_name}")
    return file_name
