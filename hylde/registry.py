import re

from hylde import lolg, settings
from hylde.downloaders import jdownloader, gallerydl

DOWNLOADER_PATTERNS = [
    (r"jpg\d+\.\w{2,8}/i(?:mg|mage)?/", gallerydl),
    (r".*", jdownloader),
]  # TODO expose setting


def get_downloader_for_url(url: str):
    for pattern, module in DOWNLOADER_PATTERNS:
        regex = re.compile(pattern)
        if regex.search(url):
            lolg.debug(
                f"Downloader '{module.__name__}' matched '{pattern}' for '{url}'"
            )
            return module
    raise ValueError(f"No downloader matched for URL: {url}")
