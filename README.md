# hylde
External Hydrus downloader.

## Development

- Install dependencies: `uv sync`
- Install Git hooks: `uv run pre-commit install`
- Run tests: `uv run pytest`
- Run type checks: `uv run ty check`
- Run locally: `uv run python hylde/server.py`

## Multipart archives

Multipart archive handling is controlled by `[multipart] enabled = true`.
When enabled, `7z`/`7zz`/`7za` must be available at startup.
Supported multipart parts currently include `*.zip.<number>` and
`*.part<number>.rar`. Incomplete-but-downloaded parts return HTTP 500 with
`Downloaded archive part; continue with remaining parts.` so Hydrus displays a
clear error message.
