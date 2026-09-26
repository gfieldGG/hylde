from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, TypeGuard, cast


class ErrorCache(TypedDict):
    error: bool
    message: str
    retryable: bool


class MultipartCache(TypedDict):
    multipart: bool
    group: str


@dataclass(frozen=True)
class DownloadError:
    """User-facing download failure returned by downloader/wrapper layers."""

    message: str
    retryable: bool = False

    def to_cache(self) -> ErrorCache:
        return {"error": True, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class MultipartAccepted:
    """A downloaded archive part was stored, but no file is ready to serve yet."""

    group: str

    def to_cache(self) -> MultipartCache:
        return {"multipart": True, "group": self.group}


@dataclass(frozen=True)
class MultipartCompleted:
    """A multipart archive completed and should update all known part URLs."""

    file_name: str
    url_keys: list[str]


DownloaderResult = list[Path] | DownloadError
WrapperResult = str | DownloadError | MultipartAccepted | MultipartCompleted
CacheEntry = str | ErrorCache | MultipartCache


def is_error_cache(value: object) -> TypeGuard[ErrorCache]:
    return (
        isinstance(value, Mapping)
        and cast(Mapping[str, object], value).get("error") is True
    )


def is_multipart_cache(value: object) -> TypeGuard[MultipartCache]:
    return (
        isinstance(value, Mapping)
        and cast(Mapping[str, object], value).get("multipart") is True
        and isinstance(cast(Mapping[str, object], value).get("group"), str)
    )
