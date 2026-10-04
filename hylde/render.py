"""JS-rendered page pass-through for Hydrus parsers, backed by a trawl instance."""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from hylde import lolg, settings

CONTENT_TYPE = "text/html; charset=utf-8"


class RenderRequestError(ValueError):
    """Malformed render request; the message is user-facing."""


class RenderFailedError(Exception):
    """trawl could not render the page; the message is user-facing."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RenderResponse:
    status_code: int
    html: str


def parse_url(args: Mapping[str, str]) -> str:
    """Return the page URL to render from GET query parameters."""
    url = args.get("url")
    if not url:
        raise RenderRequestError("Missing 'url' query parameter")
    if urlsplit(url).scheme not in ("http", "https"):
        raise RenderRequestError("'url' must be an http(s) URL.")
    return url


def _error_message(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("message") or payload)
    return str(payload)


def render(url: str) -> RenderResponse:
    """Render a page through trawl's /scrape, always using a browser.

    Raises requests.RequestException on transport failure and RenderFailedError
    when trawl answers with an error. A pool-saturated trawl (429) is reported as
    429 so Hydrus retries later.
    """
    endpoint = f"{settings.render.url.rstrip('/')}/scrape"
    payload = {
        "url": url,
        "skipHttp": True,
        "maxTimeout": int(settings.render.timeout * 1000),
    }
    lolg.info(f"Rendering '{url}' via '{endpoint}'...")
    resp = requests.post(endpoint, json=payload, timeout=settings.maxtimeout)

    if resp.status_code != 200:
        message = f"trawl failed to render page: {_error_message(resp)}"
        status = 429 if resp.status_code == 429 else 502
        raise RenderFailedError(message, status)

    result = resp.json()
    lolg.info(
        f"Rendered '{url}': status {result.get('statusCode')}, tier "
        f"{result.get('tier')}, {result.get('totalMs')} ms"
    )
    return RenderResponse(
        status_code=int(result.get("statusCode") or 200),
        html=result.get("html", ""),
    )
