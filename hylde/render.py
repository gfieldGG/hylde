"""JS-rendered page pass-through for Hydrus parsers, backed by a trawl instance."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from lxml import etree  # type:ignore
from lxml import html as lxml_html

from hylde import lolg, settings

CONTENT_TYPE = "text/html; charset=utf-8"

_DOCTYPE = re.compile(r"\s*<!doctype", re.IGNORECASE)
_SRCSET_URL = re.compile(r"[\s,]*(\S+)")


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


def _is_relative(url: str) -> bool:
    return not urlsplit(url.strip()).scheme


def _srcset_candidates(srcset: str) -> list[tuple[str, str]]:
    """Split a srcset into (url, descriptor) pairs, following the HTML parsing rules."""
    candidates = []
    pos = 0
    while match := _SRCSET_URL.match(srcset, pos):
        url, pos = match.group(1), match.end()
        descriptor = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            end = srcset.find(",", pos)
            end = len(srcset) if end == -1 else end
            descriptor, pos = srcset[pos:end].strip(), end
        if url:
            candidates.append((url, descriptor))
    return candidates


def _absolutize_srcset(srcset: str, base_url: str) -> str:
    return ", ".join(
        f"{urljoin(base_url, url)} {descriptor}".rstrip()
        for url, descriptor in _srcset_candidates(srcset)
    )


def absolutize_links(page: str, base_url: str) -> str:
    """Make relative links in the page absolute against the page's real URL.

    Hydrus resolves relative URLs against the URL it fetched, which is hylde's.
    Covers what lxml rewrites (href, src, CSS url(), ...) plus srcset and poster.
    Pages without relative links are returned unchanged; unparseable ones too.
    """
    if not page.strip():
        return page
    try:
        doc = lxml_html.document_fromstring(
            page.encode(), parser=lxml_html.HTMLParser(encoding="utf-8")
        )
    except (etree.ParserError, ValueError) as e:
        lolg.warning(f"Could not parse rendered page to absolutize links: {e}")
        return page

    srcsets = doc.xpath("//*[@srcset]")
    posters = doc.xpath("//*[@poster]")
    links = [link for _, _, link, _ in doc.iterlinks()]
    links += [el.get("poster", "") for el in posters]
    links += [
        url for el in srcsets for url, _ in _srcset_candidates(el.get("srcset", ""))
    ]
    if not any(_is_relative(link) for link in links):
        return page

    # the first <base href> applies, and may itself be relative
    base = doc.find(".//base[@href]")
    if base is not None:
        base_url = urljoin(base_url, base.get("href", "").strip())

    def absolutize(link: str) -> str:
        try:
            return urljoin(base_url, link)
        except ValueError:
            return link

    # not make_links_absolute(): it always re-applies <base href>, doubling relative ones
    doc.rewrite_links(absolutize, resolve_base_href=False)
    if base is not None:
        base.set("href", base_url)
    for el in posters:
        el.set("poster", absolutize(el.get("poster", "").strip()))
    for el in srcsets:
        el.set("srcset", _absolutize_srcset(el.get("srcset", ""), base_url))

    doctype = doc.getroottree().docinfo.doctype if _DOCTYPE.match(page) else None
    return lxml_html.tostring(doc, encoding="unicode", doctype=doctype)


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
        html=absolutize_links(result.get("html", ""), result.get("url") or url),
    )
