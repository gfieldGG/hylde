from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class ErrorCache(TypedDict):
    error: bool
    message: str
    retryable: bool


@dataclass(frozen=True)
class DownloadError:
    """User-facing download failure returned by downloader/wrapper layers."""

    message: str
    retryable: bool = False

    def to_cache(self) -> ErrorCache:
        return {"error": True, "message": self.message, "retryable": self.retryable}


DownloaderResult = list[Path] | DownloadError
WrapperResult = str | DownloadError
CacheEntry = str | ErrorCache


def is_error_cache(value: object) -> bool:
    return isinstance(value, dict) and value.get("error") is True
