import re

from hylde import lolg, settings

from hylde.downloaders import jdownloader, gallerydl  # noqa: F401


DOWNLOADER_PATTERNS = [
    (pattern, globals()[module_name])
    for pattern, module_name in settings.registry.downloader_patterns
]


def get_downloader_for_url(url: str):
    for pattern, module in DOWNLOADER_PATTERNS:
        regex = re.compile(pattern)
        if regex.search(url):
            lolg.debug(
                f"Downloader '{module.__name__}' matched '{pattern}' for '{url}'"
            )
            return module
    raise ValueError(f"No downloader matched for URL: {url}")
