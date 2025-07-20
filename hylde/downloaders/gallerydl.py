import shutil
import tempfile
from pathlib import Path

import gallery_dl as gdl  # type:ignore
import gallery_dl.path  # type:ignore

from hylde import lolg, settings


output_dir = Path(tempfile.gettempdir()) / "hylde" / "gallerydl"  # TODO expose setting

gdl.config.set(("extractor",), "base-directory", output_dir.as_posix())


class FileCollector:
    files: list[Path] = []

    def filepath_hook(self, pathfmt: gallery_dl.path.PathFormat):
        lolg.trace(f"gallerydl returned filepath: {pathfmt.path}")
        self.files.append(Path(pathfmt.path))


class GoodJob(gdl.job.DownloadJob):
    """`job.DownloadJob` with `file` hooks enabled."""

    def __init__(self, url, parent=None):
        gdl.job.Job.__init__(self, url, parent)
        self.hooks = {"file": []}
        self.log = self.get_logger("download")
        self.fallback = None
        self.archive = None
        self.sleep = None
        self.downloaders = {}
        self.out = gdl.output.select()
        self.visited = parent.visited if parent else set()
        self._extractor_filter = None
        self._skipcnt = 0


def download_url(url: str, url_key: str) -> list[Path] | None:
    """Download file for url. Return full file paths. Return empty list on retryable problems. Return None if download failed."""
    gdl.config.set(("extractor",), "directory", [url_key])
    fc = FileCollector()
    job = GoodJob(url)
    job.register_hooks(hooks={"file": fc.filepath_hook})
    job.run()

    if not fc.files:
        directory = output_dir / url_key
        if directory.exists() and any(directory.iterdir()):
            lolg.warning(
                f"gallerydl returned no filepaths for '{url_key}' but '{directory}' contained files."
            )
            lolg.warning(f"Deleting files in '{directory}' to allow retry...")
            shutil.rmtree(directory)
        else:
            lolg.error(
                f"gallerydl returned no filepaths for '{url_key}' and '{directory}' was empty."
            )
            return None

    return fc.files
