import os
import shelve
import threading
from pathlib import Path
from flask import Flask, request, send_file

from hylde import lolg, settings
from hylde.util import md5
import hylde.downloader as hydl


# initialize flask app
app = Flask(__name__)

# initialize cache directory
cache_dir = Path(settings.cachedir).resolve()
if cache_dir.exists():
    lolg.debug(f"Found temporary cache directory at '{cache_dir}'")
else:
    lolg.info(f"Creating temporary cache directory at '{cache_dir}'...")
    os.makedirs(cache_dir, exist_ok=True)

# initialize db cache
cache_file = Path(settings.cachedbfile)

# active threads registry
active_threads: dict[str, threading.Thread] = {}


def _get_file(file_name: str) -> Path:
    return (cache_dir / file_name).resolve()


def get_cached_file(url_key: str) -> str | None:
    """
    Retrieve the cached file name for a URL key from the shelve database.
    """
    lolg.debug(f"Getting cache entry for url '{url_key}'...")
    with shelve.open(cache_file) as db:
        file_name = db.get(url_key)

        if file_name is None:
            lolg.debug(f"No cache entry for url '{url_key}'")
        elif file_name == "...":
            raise DeprecationWarning("In-progress markers are obsolete.")
            lolg.debug(f"Found in-progress marker for url '{url_key}'")
        elif file_name:
            lolg.debug(f"Found cache entry '{url_key}' -> '{file_name}'")
        else:
            lolg.info(f"No file path for url '{url_key}'")
        return file_name


def set_cached_file(url_key: str, file: str | None):
    """
    Update or create a cache entry in the shelve database.
    """
    lolg.debug(f"Adding cache entry '{url_key}' -> '{file}'...")
    with shelve.open(cache_file) as db:
        db[url_key] = file


def remove_cached_file(url_key: str):
    """Delete a cache entry and its file in cache directory."""
    lolg.debug(f"Removing cache entry '{url_key}'...")
    with shelve.open(cache_file) as db:
        if url_key in db:
            if db[url_key] != "" and db[url_key] != "...":
                f = _get_file(db[url_key])
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
    url_dir = cache_dir / url_key
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
            file_name = hydl.download_file(url=url, url_key=url_key)
            if file_name is None:
                lolg.info(f"Download failed for '{url_key}'")
                set_cached_file(url_key, "FAILED")
            else:
                set_cached_file(url_key, file_name)
        except Exception as e:  # noqa: E722
            lolg.error(f"Unhandled error while downloading '{url_key}': {e}'")

    lolg.debug(f"Removing active thread '{url_key}'")
    del active_threads[url_key]


@app.route("/file", methods=["GET"])
def handle_request():
    """
    Handles file requests:
    - If the file is not downloaded yet, returns 503 (Service Unavailable).
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
    if url_key in active_threads:
        lolg.debug(f"Found active thread for url '{url_key}'")
        return "File is being downloaded. Please retry later.", 429
    lolg.debug(f"Found no active thread for '{url_key}'")

    # check if url is already cached
    cached_filename = get_cached_file(url_key=url_key)
    if not cached_filename:
        if cached_filename == "":
            lolg.warning(f"Download '{url_key}' was previously marked as unfinished.")
        lolg.info(f"Sending '{url_key}' to downloader...")
        thread = threading.Thread(target=download_file, args=(url, url_key))
        thread.start()
        active_threads[url_key] = thread
        lolg.debug(f"Started thread for '{url_key}'")
        return "Download started. Come back later.", 429

    # download has previously failed
    elif cached_filename == "FAILED":
        lolg.error(f"Previous download failed for URL '{url_key}'")
        return "Failed to download the file.", 500

    # found cache entry
    else:
        cached_file = _get_file(cached_filename)
        if not cached_file.exists():
            lolg.error(f"Cached file missing on disk: {cached_file}")
            remove_cached_file(url_key)
            return "Cached file missing on server. Downloading again.", 503

        # serve the file
        lolg.success(f"Serving file '{cached_file}' for '{url}'...")
        return send_file(cached_file)


if __name__ == "__main__":
    # start server
    app.run(host="0.0.0.0", port=settings.port)
