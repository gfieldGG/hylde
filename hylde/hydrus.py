"""Hydrus Client API calls that hand a rendered page's browser session to Hydrus."""

from urllib.parse import urlsplit

import requests

from hylde import lolg, settings

ACCESS_KEY_HEADER = "Hydrus-Client-API-Access-Key"
API_TIMEOUT = 10


class HydrusAPIError(Exception):
    """The Hydrus Client API rejected a request."""


def _client_api() -> tuple[str, str] | None:
    render_settings = getattr(settings, "render", None)
    url = getattr(render_settings, "hydrus_url", "") or ""
    key = getattr(render_settings, "hydrus_key", "") or ""
    if not url or not key:
        return None
    return url.rstrip("/"), key


def is_configured() -> bool:
    return _client_api() is not None


def _domain_matches(host: str, cookie_domain: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    return host == domain or host.endswith(f".{domain}")


def session_cookies(cookies: list[dict], page_url: str) -> list[list]:
    """Hydrus cookie rows for the cookies a browser would send to the page's host.

    Third-party cookies from the page load are left out. Rows are
    [name, value, domain, path, expires]; trawl marks session cookies with -1.
    """
    host = (urlsplit(page_url).hostname or "").lower()
    rows = []
    for cookie in cookies:
        domain = cookie.get("domain") or ""
        if not domain or not _domain_matches(host, domain):
            continue
        expires = cookie.get("expires")
        rows.append(
            [
                cookie["name"],
                cookie["value"],
                domain,
                cookie.get("path") or "/",
                int(expires) if expires is not None and expires >= 0 else None,
            ]
        )
    return rows


def _post(endpoint: str, payload: dict) -> None:
    api = _client_api()
    if api is None:
        raise HydrusAPIError("Hydrus Client API is not configured")
    base_url, key = api
    resp = requests.post(
        f"{base_url}{endpoint}",
        json=payload,
        headers={ACCESS_KEY_HEADER: key},
        timeout=API_TIMEOUT,
    )
    if not resp.ok:
        raise HydrusAPIError(f"{endpoint} returned {resp.status_code}: {resp.text}")


def share_session(cookies: list[dict], user_agent: str, page_url: str) -> None:
    """Copy the page's cookies, and the user agent they belong to, into Hydrus.

    Hydrus then sends them on its own requests to those domains, e.g. a final
    download from the same session. Raises HydrusAPIError or
    requests.RequestException.
    """
    rows = session_cookies(cookies, page_url)
    if not rows:
        lolg.info(f"No session cookies to share with Hydrus for '{page_url}'")
        return

    _post("/manage_cookies/set_cookies", {"cookies": rows})
    domains = sorted({row[2].lstrip(".") for row in rows})
    if user_agent:
        for domain in domains:
            _post(
                "/manage_headers/set_headers",
                {"domain": domain, "headers": {"User-Agent": {"value": user_agent}}},
            )
    lolg.info(f"Shared {len(rows)} cookies with Hydrus for {', '.join(domains)}")
