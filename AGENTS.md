# AGENTS.md

## What is hylde?

A small Flask HTTP server that acts as an **external downloader for Hydrus**.
Clients request files via `GET /file?url=...`. The server downloads the URL
asynchronously, caches it, and serves it back. If the download is not ready,
the client gets `429` and is expected to retry.

## Dependencies

- **Hydrus** — the client that calls `/file?url=` and expects 429 polling.
- **gallery-dl** — Python package (git master) for `gallerydl.py` downloader.
- **MyJDownloader** — optional remote API for `jdownloader.py` downloader.

## HTTP API

- **`GET /file?url=<url>`**
  - `200` — file is cached and served.
  - `429` — download in progress; client should retry.
  - `503` — retryable failure (empty cache entry or missing file on disk).
  - `500` — permanent failure (`FAILED` cache entry).
  - `400` — missing `url` query parameter.

- **`GET /shim`**
  - `200` — blank page (used by Hydrus URL parsing).

## Architecture

```
HTTP request -> server.py -> wrapper.py -> registry.py -> downloader module
                                              |
                                              v
                                       filesystem cache + shelve DB
```

- **`server.py`** — Flask app, request lifecycle, threaded downloads, cache lookup.
- **`wrapper.py`** — Orchestrates download → cache. Moves single files or zips
  multiples into the cache directory.
- **`registry.py`** — Routes URLs to downloader modules via regex patterns from
  config.
- **`downloaders/gallerydl.py`** — gallery-dl integration. Detects
  `IncompleteRead` and treats it as retryable (returns `[]`).
- **`downloaders/jdownloader.py`** — MyJDownloader integration. Polls JD for
  package completion.
- **`util.py`** — Single `md5()` helper.

## Configuration

Dynaconf loads `config.toml`, then `config.dev.toml`, then `/config/config.toml`.
Env vars prefixed with `HYLDE_` override everything.

Key settings:
- `cachedir` — Where downloaded files live.
- `cachedbfile` — Shelve database path for URL→file mappings.
- `maxtimeout` — Seconds to block the HTTP request waiting for a download.
- `registry.downloader_patterns` — List of `[regex, module_name]` tuples.

## Conventions

- **Settings are read at runtime, not import time.**
  `wrapper.py` and `server.py` use `_cache_dir()` / `_cache_file()` functions
  rather than module-level constants. This makes tests patchable without
  import-order gymnastics.
- **Cache lifecycle (shelve DB):**
  - `None` — URL never seen; start download.
  - `""` — retryable failure; next request retries (503).
  - `"FAILED"` — permanent failure; next request retries (500).
  - `"<url_key>/filename"` — success; serve file from cache.
- **Return values from `download_url`:**
  - `list[Path]` — success (1 file = moved, >1 = zipped)
  - `[]` — retryable failure (client gets 503, may retry)
  - `None` — permanent failure (client gets 500)
- **Logging:** Loguru everywhere. `hylde/__init__.py` bridges stdlib
  `logging` (from gallery-dl) into Loguru via `_InterceptHandler`.

## Testing

- **Runner:** `poetry run pytest`
- **Coverage:** `wrapper.py`, `registry.py`, `server.py`, `downloaders/gallerydl.py`.
  `jdownloader.py` is excluded (requires live MyJDownloader API).
- **Patterns:**
  - Patch `hylde.wrapper._cache_dir` and `hylde.server._cache_dir` /
    `hylde.server._cache_file` to redirect FS operations into `tmp_path`.
  - Use Flask's `test_client()` for route tests.
  - Mock `threading.Thread` when testing the 429 "still downloading" path.

## Development

- **Install:** `poetry install`
- **Test:** `poetry run pytest`
- **Run locally:** `poetry run python hylde/server.py`
- **Credentials:** `config.dev.toml` exists locally with MyJDownloader credentials. Never commit it.

## Build & Deploy

- **Package manager:** Poetry.
- **Container:** `Dockerfile` builds a slim Python 3.14 image.
- **CI:** `.github/workflows/docker-build.yml` pushes to GHCR on tag push.
- **Entrypoint:** `poetry run python hylde/server.py`

## Notes for agents

- Do **not** run `git commit` / `git push` unless explicitly asked.
- Ask before modifying `jdownloader.py` — it contains API credentials in
  `config.dev.toml` (not committed, but present locally).
- If a test fails, report it before iterating. Do not blindly fix tests.
