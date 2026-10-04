import os
import shelve
import threading
from pathlib import Path

import requests
from flask import Flask, Response, request, send_file

import hylde.wrapper as hydl
from hylde import lolg, multipart, post, render, settings
from hylde.result import (
    CacheEntry,
    DownloadError,
    MultipartAccepted,
    MultipartCompleted,
    is_error_cache,
    is_multipart_cache,
)
from hylde.util import md5

# initialize flask app
app = Flask(__name__)


def _cache_dir() -> Path:
    return Path(settings.cachedir).resolve()


def _cache_file() -> Path:
    return Path(settings.cachedbfile)


# initialize cache directory
_cache_dir_init = _cache_dir()
if _cache_dir_init.exists():
    lolg.debug(f"Found temporary cache directory at '{_cache_dir_init}'")
else:
    lolg.info(f"Creating temporary cache directory at '{_cache_dir_init}'...")
    os.makedirs(_cache_dir_init, exist_ok=True)

# active threads registry
active_threads: dict[str, threading.Thread] = {}


def _get_file(file_name: str) -> Path:
    return (_cache_dir() / file_name).resolve()


def get_cached_file(url_key: str) -> CacheEntry | None:
    """
    Retrieve the cached file name for a URL key from the shelve database.
    """
    lolg.debug(f"Looking for cache entry for url '{url_key}'...")
    with shelve.open(_cache_file()) as db:
        file_name = db.get(url_key)

        if file_name is None:
            lolg.debug(f"No cache entry for url '{url_key}'")
        elif file_name == "...":
            raise DeprecationWarning("In-progress markers are obsolete.")
            lolg.debug(f"Found in-progress marker for url '{url_key}'")
        elif is_error_cache(file_name):
            lolg.info(
                f"Found failed cache entry for url '{url_key}': "
                f"{file_name.get('message', 'Download failed.')}"
            )
        elif is_multipart_cache(file_name):
            lolg.debug(
                f"Found multipart cache entry for url '{url_key}': "
                f"{file_name.get('group')}"
            )
        elif file_name:
            lolg.debug(f"Found cache entry '{url_key}' -> '{file_name}'")
        else:
            lolg.info(f"No file path for url '{url_key}'")
        return file_name


def set_cached_file(url_key: str, file: CacheEntry):
    """
    Update or create a cache entry in the shelve database.
    """
    lolg.debug(f"Adding cache entry '{url_key}' -> '{file}'...")
    with shelve.open(_cache_file()) as db:
        db[url_key] = file


def remove_cached_file(url_key: str):
    """Delete a cache entry and its file in cache directory."""
    lolg.debug(f"Removing cache entry '{url_key}'...")
    with shelve.open(_cache_file()) as db:
        if url_key in db:
            entry = db[url_key]
            if isinstance(entry, str) and entry not in ("", "...", "FAILED"):
                f = _get_file(entry)
                if f.exists():
                    f.unlink()
                    lolg.debug(f"Deleted file '{f}' for '{url_key}'")
                    # TODO delete job directory if empty
            del db[url_key]
            lolg.debug(f"Deleted cache entry '{url_key}'")


def normalize_url(url: str) -> str:
    normalized = url
    lolg.debug(f"Normalized url '{url}' -> '{normalized}'")
    return normalized


def get_url_key(url: str) -> str:
    return md5(url)


def look_in_cache_directory(url_key: str) -> str | None:
    """Return first file in cachedir/url_key/ directory. Potentially unsafe."""
    url_dir = _cache_dir() / url_key
    if url_dir.exists():
        file = next(url_dir.iterdir(), None)
        if file:
            file_name = f"{url_key}/{file.name}"

            return file_name
    return None


def download_file(url, url_key):
    if file_name := look_in_cache_directory(url_key):
        set_cached_file(url_key, file_name)
        lolg.success(f"Recovered file '{file_name}' for url_key '{url_key}'")
    else:
        try:
            result = hydl.download_file(url=url, url_key=url_key)
            if isinstance(result, DownloadError):
                lolg.info(f"Download failed for '{url_key}': {result.message}")
                set_cached_file(url_key, result.to_cache())
            elif isinstance(result, MultipartAccepted):
                lolg.info(
                    f"Multipart part accepted for '{url_key}' in group '{result.group}'"
                )
                set_cached_file(url_key, result.to_cache())
            elif isinstance(result, MultipartCompleted):
                lolg.info(
                    f"Multipart archive completed for '{url_key}': {result.file_name}"
                )
                for completed_url_key in sorted(set(result.url_keys) | {url_key}):
                    set_cached_file(completed_url_key, result.file_name)
            else:
                set_cached_file(url_key, result)
        except Exception as e:  # noqa: BLE001
            lolg.error(f"Unhandled error while downloading '{url_key}': {e}'")
            set_cached_file(url_key, DownloadError(str(e), retryable=True).to_cache())

    lolg.debug(f"Removing active thread '{url_key}'")
    del active_threads[url_key]


@app.route("/file", methods=["GET"])
def handle_request():
    """
    Handles file requests:
    - If the file is not downloaded yet, returns 429.
    - If the file is downloaded, serves the file.
    """
    url = request.args.get("url")
    if not url:
        lolg.error("Missing 'url' query parameter.")
        return "Missing 'url' query parameter", 400

    url = normalize_url(url)
    url_key = get_url_key(url)
    lolg.info(f"Received request for url '{url_key}' ({url})")

    # check if there is an active downloader
    if thread := active_threads.get(url_key):
        lolg.debug(
            f"Found active thread for url '{url_key}'. Waiting for up to {settings.maxtimeout} seconds..."
        )
        # wait for the thread to finish during this request
        thread.join(timeout=settings.maxtimeout)
        if thread.is_alive():
            lolg.debug(
                f"Download '{url_key}' still not finished after {settings.maxtimeout} seconds."
            )
            return "File is being downloaded. Please retry later.", 429
        lolg.debug(f"Download '{url_key}' seems to have finished now.")
    else:
        lolg.debug(f"Found no active thread for '{url_key}'")

    # check if url is already cached
    cached_filename = get_cached_file(url_key=url_key)

    # url not seen before
    if cached_filename is None:
        lolg.info(f"Sending '{url_key}' to downloader...")
        thread = threading.Thread(target=download_file, args=(url, url_key))
        thread.start()
        active_threads[url_key] = thread
        lolg.debug(
            f"Started thread for '{url_key}'. Waiting for up to {settings.maxtimeout} seconds for finish..."
        )
        # wait for the thread to finish during this request
        thread.join(timeout=settings.maxtimeout)
        if thread.is_alive():
            lolg.debug(
                f"Download '{url_key}' not finished after {settings.maxtimeout} seconds."
            )
            return "Download started. Come back later.", 429
        else:
            lolg.debug(
                f"Download '{url_key}' finished within the initial {settings.maxtimeout} seconds."
            )
            cached_filename = get_cached_file(url_key=url_key)

    # download has previously failed with a user-facing message
    if is_error_cache(cached_filename):
        message = str(cached_filename.get("message", "Download failed."))
        retryable = bool(cached_filename.get("retryable", False))
        status_code = 503 if retryable else 500
        lolg.warning(f"Previous download failed for '{url_key}': {message}")
        remove_cached_file(url_key=url_key)
        return message, status_code

    if is_multipart_cache(cached_filename):
        group = cached_filename["group"]
        final_path = multipart.get_final_cache_path(group)
        if error := multipart.get_group_error(group):
            lolg.warning(
                f"Multipart group '{group}' failed for '{url_key}': {error.message}"
            )
            set_cached_file(url_key, error.to_cache())
            return error.message, 503 if error.retryable else 500
        elif final_path:
            lolg.info(
                f"Multipart group '{group}' completed; updating '{url_key}' -> "
                f"'{final_path}'"
            )
            set_cached_file(url_key, final_path)
            cached_filename = final_path
        elif not multipart.has_url_key(group, url_key):
            lolg.warning(
                f"Multipart part for '{url_key}' is missing from group '{group}'. "
                "Clearing cache entry so it can be downloaded again."
            )
            remove_cached_file(url_key=url_key)
            return "Multipart state missing. Please try again.", 503
        else:
            lolg.info(
                f"Multipart group '{group}' accepted for '{url_key}', but no "
                "output is ready yet."
            )
            return "Downloaded archive part; continue with remaining parts.", 500

    # legacy retryable failure marker
    if cached_filename == "":
        lolg.warning(f"Download '{url_key}' was previously marked as retryable.")
        remove_cached_file(url_key=url_key)
        return "Download previously failed. You may try again.", 503

    # legacy permanent failure marker
    elif cached_filename == "FAILED":
        lolg.error(f"Previous download failed for '{url_key}'")
        remove_cached_file(url_key=url_key)
        return "Failed to download the file.", 500

    if cached_filename is None:
        lolg.error(f"Download finished without a cache entry for '{url_key}'")
        return "Download did not produce a cache entry. Please try again.", 503

    if not isinstance(cached_filename, str):
        lolg.error(f"Unexpected cache entry for '{url_key}': {cached_filename}")
        remove_cached_file(url_key=url_key)
        return "Unexpected cache entry. Please try again.", 503

    # found cache entry
    cached_file = _get_file(cached_filename)
    if not cached_file.exists():
        lolg.error(f"Cached file missing on disk: {cached_file}")
        remove_cached_file(url_key)
        return "Cached file missing on server. Please try again.", 503

    # serve the file
    lolg.success(f"Serving file '{cached_file}' for '{url}'...")
    return send_file(cached_file)


@app.route("/post", methods=["GET"])
def handle_post():
    """Forward a GET request as a POST upstream and pass the response through."""
    try:
        upstream = post.parse_request(request.args)
    except post.PostRequestError as e:
        lolg.error(f"Invalid /post request: {e}")
        return str(e), 400

    try:
        resp = post.send(upstream)
    except requests.RequestException as e:
        lolg.error(f"POST to '{upstream.url}' failed: {e}")
        return f"Upstream request failed: {e}", 502

    return Response(
        resp.content,
        status=resp.status_code,
        content_type=resp.content_type or "application/octet-stream",
    )


@app.route("/render", methods=["GET"])
@app.route("/render/<site>", methods=["GET"])
def handle_render(site: str | None = None):
    """Return a page's HTML after JavaScript ran, rendered by trawl.

    The optional path segment only lets Hydrus link a separate URL class and
    parser per site.
    """
    try:
        url = render.parse_url(request.args)
    except render.RenderRequestError as e:
        lolg.error(f"Invalid /render request: {e}")
        return str(e), 400

    if site:
        lolg.info(f"Render request for site '{site}'")
    try:
        page = render.render(url)
    except render.RenderFailedError as e:
        lolg.error(f"Rendering '{url}' failed: {e}")
        return str(e), e.status_code
    except requests.Timeout as e:
        lolg.error(f"Rendering '{url}' timed out: {e}")
        return f"Render request timed out: {e}", 504
    except requests.RequestException as e:
        lolg.error(f"Rendering '{url}' failed: {e}")
        return f"Render request failed: {e}", 502

    return Response(
        page.html, status=page.status_code, content_type=render.CONTENT_TYPE
    )


@app.route("/shim")
def blank_page():
    """Return a successful blank page for hydrus url parsing shenanigans."""
    return " ", 200  # blank page with a 200 HTTP status code


if __name__ == "__main__":
    # start server
    app.run(host="0.0.0.0", port=settings.port)
