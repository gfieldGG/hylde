import importlib
import importlib.util
import re
from pathlib import Path

from hylde import lolg, settings


def _plugin_modules() -> dict:
    return dict(getattr(settings.registry, "downloader_modules", {}) or {})


def _is_file_module(module_ref: str) -> bool:
    return module_ref.endswith(".py") or "/" in module_ref or "\\" in module_ref


def _load_file_module(name: str, module_ref: str):
    path = Path(module_ref).expanduser()
    if not path.exists():
        raise ValueError(f"Downloader '{name}' not found at '{path}'.")

    spec = importlib.util.spec_from_file_location(f"hylde_downloader_{name}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Downloader '{name}' could not be loaded.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_downloader(name: str):
    plugins = _plugin_modules()
    module_ref = plugins.get(name)

    if module_ref:
        if _is_file_module(module_ref):
            module = _load_file_module(name, module_ref)
        else:
            module = importlib.import_module(module_ref)
    else:
        module = importlib.import_module(f"hylde.downloaders.{name}")

    if not callable(getattr(module, "download_url", None)):
        raise ValueError(f"Downloader '{name}' does not define download_url().")

    return module


DOWNLOADER_PATTERNS = [
    (pattern, load_downloader(module_name))
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
