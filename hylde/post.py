"""Generic POST pass-through for Hydrus parsers, which can only issue GET requests."""

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

from hylde import lolg, settings

DEFAULT_CONTENT_TYPE = "application/json"
STRING_VAR_PREFIX = "var_"
JSON_VAR_PREFIX = "varjson_"

_B64_TO_STANDARD = str.maketrans("-_", "+/")


class PostRequestError(ValueError):
    """Malformed pass-through request; the message is user-facing."""


@dataclass(frozen=True)
class PostRequest:
    url: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PostResponse:
    status_code: int
    content: bytes
    content_type: str | None


def b64decode(value: str) -> bytes:
    """Decode base64 or base64url, with or without padding.

    Spaces are turned back into '+' in case a standard base64 value was not
    percent-encoded and got form-decoded.
    """
    value = value.replace(" ", "+").strip().translate(_B64_TO_STANDARD)
    value += "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as e:
        raise PostRequestError(f"Invalid base64: {e}") from e


def _is_json_content_type(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower().endswith("json")


def _parse_headers(value: str) -> dict[str, str]:
    try:
        headers = json.loads(b64decode(value))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PostRequestError(f"'headers' is not valid JSON: {e}") from e
    if not isinstance(headers, dict) or not all(
        isinstance(v, str) for v in headers.values()
    ):
        raise PostRequestError("'headers' must be a JSON object of strings.")
    return headers


def _collect_variables(args: Mapping[str, str]) -> dict[str, object]:
    variables: dict[str, object] = {}
    for key, value in args.items():
        if key.startswith(JSON_VAR_PREFIX):
            name = key.removeprefix(JSON_VAR_PREFIX)
            try:
                parsed: object = json.loads(value)
            except json.JSONDecodeError as e:
                raise PostRequestError(f"'{key}' is not valid JSON: {e}") from e
        elif key.startswith(STRING_VAR_PREFIX):
            name = key.removeprefix(STRING_VAR_PREFIX)
            parsed = value
        else:
            continue
        if not name:
            raise PostRequestError(f"'{key}' is missing a variable name.")
        if name in variables:
            raise PostRequestError(f"Variable '{name}' is set more than once.")
        variables[name] = parsed
    return variables


def _merge_variables(body: bytes, variables: dict[str, object]) -> bytes:
    try:
        payload = json.loads(body) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PostRequestError(f"'body' is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise PostRequestError("'body' must be a JSON object to merge variables.")
    existing = payload.get("variables") or {}
    if not isinstance(existing, dict):
        raise PostRequestError("'body.variables' must be a JSON object.")
    payload["variables"] = existing | variables
    return json.dumps(payload, separators=(",", ":")).encode()


def parse_request(args: Mapping[str, str]) -> PostRequest:
    """Build the upstream request from GET query parameters."""
    url = args.get("url")
    if not url:
        raise PostRequestError("Missing 'url' query parameter")
    if urlsplit(url).scheme not in ("http", "https"):
        raise PostRequestError("'url' must be an http(s) URL.")

    content_type = args.get("content_type") or DEFAULT_CONTENT_TYPE
    body = b64decode(args["body"]) if args.get("body") else b""

    if variables := _collect_variables(args):
        if not _is_json_content_type(content_type):
            raise PostRequestError(
                f"Variables require a JSON content type, got '{content_type}'."
            )
        body = _merge_variables(body, variables)

    headers = {"Content-Type": content_type}
    if args.get("headers"):
        headers |= _parse_headers(args["headers"])

    return PostRequest(url=url, body=body, headers=headers)


def send(post: PostRequest) -> PostResponse:
    """POST upstream; raises requests.RequestException on transport failure."""
    lolg.info(f"POSTing {len(post.body)} bytes to '{post.url}'...")
    resp = requests.post(
        post.url, data=post.body, headers=post.headers, timeout=settings.maxtimeout
    )
    lolg.info(f"Upstream '{post.url}' answered {resp.status_code}")
    return PostResponse(
        status_code=resp.status_code,
        content=resp.content,
        content_type=resp.headers.get("Content-Type"),
    )
