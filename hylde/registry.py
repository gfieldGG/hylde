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
        builtin_ref = f"hylde.downloaders.{name}"
        try:
            module = importlib.import_module(builtin_ref)
        except ModuleNotFoundError as exc:
            if exc.name == builtin_ref:
                raise ValueError(
                    f"Downloader '{name}' is not built in and no plugin is configured. "
                    f'Add [registry.downloader_modules] {name} = "/path/to/{name}.py" '
                    "or change registry.downloader_patterns."
                ) from exc
            raise

    if not callable(getattr(module, "download_url", None)):
        raise ValueError(f"Downloader '{name}' does not define download_url().")

    return module


def _build_downloader_patterns():
    patterns = []
    loaded = []
    for pattern, module_name in settings.registry.downloader_patterns:
        try:
            module = load_downloader(module_name)
            patterns.append((pattern, module))
            loaded.append(f"{module_name}={module.__name__}")
        except Exception as exc:
            lolg.critical(
                f"Failed to load downloader '{module_name}' for pattern "
                f"'{pattern}': {exc}"
            )
            raise

    lolg.info(f"Loaded downloaders: {', '.join(loaded)}")
    return patterns


DOWNLOADER_PATTERNS = _build_downloader_patterns()


def get_downloader_for_url(url: str):
    for pattern, module in DOWNLOADER_PATTERNS:
        regex = re.compile(pattern)
        if regex.search(url):
            lolg.debug(
                f"Downloader '{module.__name__}' matched '{pattern}' for '{url}'"
            )
            return module
    raise ValueError(f"No downloader matched for URL: {url}")
