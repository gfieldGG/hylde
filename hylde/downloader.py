from pathlib import Path

from hylde import lolg, settings

import hylde.jdownloader as hyjdl


cache_directory = Path(settings.cachedir).resolve()


def download_file(url: str, url_key: str) -> str:
    file_names = hyjdl.download_url(url, url_key, cache_directory)

    if not file_names:
        lolg.error(f"Error while downloading '{url}'")
        return ""
    if len(file_names) > 1:
        lolg.error("Cannot handle more than one downloaded file per url.")
        return ""

    file_name = file_names[0]

    return file_name
