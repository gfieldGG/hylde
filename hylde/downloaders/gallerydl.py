import tempfile
import uuid
from pathlib import Path

import gallery_dl as gdl  # type:ignore
import gallery_dl.path  # type:ignore

from hylde import lolg


output_dir = Path(tempfile.gettempdir()) / "hylde" / "gallerydl"  # TODO expose setting

gdl.config.set(("extractor",), "base-directory", output_dir.as_posix())
gdl.config.set(("extractor",), "retries", 0)
gdl.config.set(("downloader",), "retries", 0)
gdl.config.set(("output",), "mode", "null")


class FileCollector:
    url_key: str
    files: list[Path]
    errors: list[Path]

    def __init__(self, url_key):
        self.url_key = url_key
        self.files = []
        self.errors = []
        lolg.debug(f"Created FileCollector for '{url_key}'")

    def filepath_hook(self, pathfmt: gallery_dl.path.PathFormat):
        lolg.debug(f"[{self.url_key}] gallerydl returned filepath: {pathfmt.path}")
        self.files.append(Path(pathfmt.path))

    def error_hook(self, pathfmt: gallery_dl.path.PathFormat):
        lolg.debug(f"[{self.url_key}] gallerydl returned error for: {pathfmt.path}")
        self.errors.append(Path(pathfmt.path))


class _IncompleteReadAdapter(gdl.output.LoggerAdapter):
    """Detect IncompleteRead messages in gallery-dl logs."""

    def __init__(self, logger, job):
        super().__init__(logger, job)
        self._job = job

    def _check(self, msg, args):
        if "IncompleteRead" in str(msg):
            self._job.has_incomplete_read = True
            return
        for arg in args:
            if "IncompleteRead" in str(arg):
                self._job.has_incomplete_read = True
                return

    def warning(self, msg, *args, **kwargs):
        self._check(msg, args)
        super().warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._check(msg, args)
        super().error(msg, *args, **kwargs)


class GoodJob(gdl.job.DownloadJob):
    """`job.DownloadJob` with `file` hooks enabled."""

    def __init__(self, url, parent=None):
        gdl.job.Job.__init__(self, url, parent)
        self.hooks = {"file": [], "error": []}
        self.log = self.get_logger("download")
        self.fallback = None
        self.archive = None
        self.sleep = None
        self.downloaders = {}
        self.out = gdl.output.select()
        self.visited = parent.visited if parent else set()
        self._extractor_filter = None
        self._skipcnt = 0
        self.has_incomplete_read = False

    def _wrap_logger(self, logger):
        return _IncompleteReadAdapter(logger, self)


def download_url(url: str, url_key: str) -> list[Path] | None:
    """Download file for url. Return full file paths. Return empty list on retryable problems. Return None if download failed."""
    gdl.config.set(("extractor",), "directory", [f"{uuid.uuid4()}"])
    fc = FileCollector(url_key=url_key)
    job = GoodJob(url)
    job.register_hooks(hooks={"file": fc.filepath_hook, "error": fc.error_hook})
    job.run()

    if job.has_incomplete_read:
        lolg.warning(
            f"gallerydl IncompleteRead for '{url_key}' — treating as retryable"
        )
        for f in fc.files:
            if f.exists():
                f.unlink()
                lolg.debug(f"Deleted partial temp file '{f}'")
        return []

    if fc.errors:
        lolg.error(f"gallerydl returned {len(fc.errors)} errors for '{url_key}'")
        return None

    if not fc.files:
        lolg.error(f"gallerydl returned no filepaths for '{url_key}'.")

    return fc.files
