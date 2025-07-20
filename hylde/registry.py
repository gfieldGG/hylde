import re

from hylde.downloaders import jdownloader, gallerydl

DOWNLOADER_PATTERNS = [
    (re.compile(r".*"), jdownloader),
]


def get_downloader_for_url(url: str):
    for pattern, module in DOWNLOADER_PATTERNS:
        if pattern.match(url):
            return module
    raise ValueError(f"No downloader matched for URL: {url}")
