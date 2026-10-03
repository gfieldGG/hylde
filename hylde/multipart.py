from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hylde import lolg, settings
from hylde.result import DownloadError, MultipartAccepted
from hylde.util import md5

_ZIP_PART_RE = re.compile(r"^(?P<stem>.+\.zip)\.(?P<part>\d+)$", re.IGNORECASE)
_RAR_PART_RE = re.compile(r"^(?P<prefix>.+)\.part(?P<part>\d+)\.rar$", re.IGNORECASE)
_EXTRACTOR_CANDIDATES = ("7z", "7zz", "7za")

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()
_FINALIZING: set[str] = set()
_FINALIZING_LOCK = threading.Lock()


@dataclass(frozen=True)
class ArchivePart:
    group: str
    archive_name: str
    part_number: int
    host: str
    filename: str


@dataclass(frozen=True)
class MultipartExtractionReady:
    group: str
    files: list[Path]


@dataclass(frozen=True)
class MultipartAlreadyComplete:
    group: str
    final_cache_path: str


MultipartProcessResult = (
    MultipartAccepted
    | MultipartExtractionReady
    | MultipartAlreadyComplete
    | DownloadError
)


def is_enabled() -> bool:
    multipart_settings = getattr(settings, "multipart", None)
    return bool(multipart_settings and getattr(multipart_settings, "enabled", False))


def find_extractor() -> str | None:
    for candidate in _EXTRACTOR_CANDIDATES:
        if executable := shutil.which(candidate):
            return executable
    return None


def _cache_dir() -> Path:
    return Path(settings.cachedir).resolve()


def _work_root() -> Path:
    return _cache_dir() / "_multipart"


def _lock_for_group(group: str) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(group, threading.Lock())


def _set_finalizing(group: str):
    with _FINALIZING_LOCK:
        _FINALIZING.add(group)


def _clear_finalizing(group: str):
    with _FINALIZING_LOCK:
        _FINALIZING.discard(group)


def _is_finalizing(group: str) -> bool:
    with _FINALIZING_LOCK:
        return group in _FINALIZING


def detect_archive_part(file_path: Path, url: str) -> ArchivePart | None:
    """Return archive-part metadata for supported multipart archive names."""
    filename = file_path.name
    match = _ZIP_PART_RE.match(filename)
    if match:
        archive_name = match.group("stem")
        part_number = int(match.group("part"))
    else:
        match = _RAR_PART_RE.match(filename)
        if not match:
            return None
        archive_name = f"{match.group('prefix')}.rar"
        part_number = int(match.group("part"))

    host = (urlparse(url).hostname or "unknown").casefold()
    group = md5(f"{host}/{archive_name.casefold()}")
    return ArchivePart(group, archive_name, part_number, host, filename)


def _metadata_error(metadata: dict[str, Any]) -> DownloadError | None:
    error = metadata.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return DownloadError(
            error["message"], retryable=bool(error.get("retryable", False))
        )
    return None


def _looks_incomplete(output: str) -> bool:
    lower = output.casefold()
    return any(
        marker in lower
        for marker in (
            "unexpected end",
            "missing volume",
            "cannot find archive",
            "can not open the file as archive",
            "unexpected end of archive",
            "data after the end of archive",
        )
    )


def _looks_permanent(output: str) -> bool:
    lower = output.casefold()
    return any(
        marker in lower
        for marker in (
            "wrong password",
            "encrypted",
            "unsupported method",
            "crc failed",
        )
    )


class MultipartJob:
    """One temporary job collecting parts for one multipart archive."""

    def __init__(self, group: str):
        self.group = group
        self.root = _work_root() / group
        self.metadata_file = self.root / "metadata.json"
        self.parts_dir = self.root / "parts"
        self.extract_dir = self.root / "extract"

    def load(self) -> dict[str, Any]:
        if not self.metadata_file.exists():
            return {"group": self.group, "parts": {}}
        with self.metadata_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, metadata: dict[str, Any]):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_path = self.metadata_file.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, sort_keys=True)
        os.replace(tmp_path, self.metadata_file)

    def reset(self):
        shutil.rmtree(self.root, ignore_errors=True)
        _clear_finalizing(self.group)

    def prune_missing_parts(self, metadata: dict[str, Any]) -> list[str]:
        """Drop parts whose files vanished (e.g. cache cleanup); keep the rest."""
        parts = metadata.get("parts", {})
        missing = [
            number
            for number, part in parts.items()
            if not (self.parts_dir / str(part.get("filename", ""))).is_file()
        ]
        for number in missing:
            lolg.warning(
                f"Multipart part {number} of '{self.group}' is missing on disk; "
                "dropping it so it can be downloaded again."
            )
            del parts[number]
        return missing

    def has_url_key(self, url_key: str) -> bool:
        """Return whether url_key is backed by a stored part of this group."""
        if not self.metadata_file.exists():
            return False
        metadata = self.load()
        if self.prune_missing_parts(metadata):
            if any(
                metadata.get(key)
                for key in ("parts", "late_url_keys", "final_cache_path", "error")
            ):
                self.save(metadata)
            else:
                self.reset()
                return False
        if url_key in metadata.get("late_url_keys", []) and _is_finalizing(self.group):
            return True
        return any(
            url_key in part.get("url_keys", [])
            for part in metadata.get("parts", {}).values()
        )

    def final_path(self) -> str | None:
        metadata = self.load()
        final_path = metadata.get("final_cache_path")
        if not isinstance(final_path, str) or not final_path:
            return None
        if (_cache_dir() / final_path).exists():
            return final_path
        self.reset()
        return None

    def error(self) -> DownloadError | None:
        return _metadata_error(self.load())

    def record_late_url_key(self, metadata: dict[str, Any], url_key: str):
        late_url_keys = set(metadata.get("late_url_keys", []))
        late_url_keys.add(url_key)
        metadata["late_url_keys"] = sorted(late_url_keys)

    def known_url_keys(self, metadata: dict[str, Any]) -> list[str]:
        url_keys = set(metadata.get("late_url_keys", []))
        for part in metadata.get("parts", {}).values():
            url_keys.update(str(key) for key in part.get("url_keys", []))
        return sorted(url_keys)

    def store_part(
        self, metadata: dict[str, Any], part: ArchivePart, url_key: str, file_path: Path
    ) -> DownloadError | None:
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        target_path = self.parts_dir / part.filename

        if target_path.exists():
            if target_path.stat().st_size != file_path.stat().st_size:
                file_path.unlink(missing_ok=True)
                return DownloadError(
                    "Multipart archive part collision.", retryable=False
                )
            if file_path.resolve() != target_path.resolve():
                file_path.unlink(missing_ok=True)
        else:
            shutil.move(file_path, target_path)

        parts = metadata.setdefault("parts", {})
        part_key = str(part.part_number)
        existing = parts.get(part_key)
        if existing and existing.get("filename") != target_path.name:
            target_path.unlink(missing_ok=True)
            return DownloadError("Multipart archive part collision.", retryable=False)

        entry = parts.setdefault(
            part_key,
            {"filename": target_path.name, "size": target_path.stat().st_size},
        )
        entry["url_keys"] = sorted(set(entry.get("url_keys", [])) | {url_key})
        metadata.setdefault("host", part.host)
        metadata.setdefault("archive_name", part.archive_name)
        return None

    def contiguous_part_paths(self, metadata: dict[str, Any]) -> list[Path] | None:
        parts = metadata.get("parts", {})
        if "1" not in parts:
            return None
        numbers = sorted(int(number) for number in parts)
        if numbers != list(range(1, numbers[-1] + 1)):
            return None
        return [self.parts_dir / parts[str(number)]["filename"] for number in numbers]

    def extracted_files(self) -> list[Path] | DownloadError:
        extract_root = self.extract_dir.resolve()
        files: list[Path] = []
        for path in self.extract_dir.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                continue
            if path.is_symlink():
                return DownloadError(
                    "Multipart archive extracted an unsafe file.", retryable=False
                )
            if not path.is_file():
                return DownloadError(
                    "Multipart archive extracted an unsupported file.", retryable=False
                )
            if not path.resolve().is_relative_to(extract_root):
                return DownloadError(
                    "Multipart archive extracted an unsafe file.", retryable=False
                )
            files.append(path)
        return sorted(files)

    def attempt_extraction(self, part_paths: list[Path]) -> MultipartProcessResult:
        extractor = find_extractor()
        if extractor is None:
            return DownloadError("7z executable not found.", retryable=False)

        shutil.rmtree(self.extract_dir, ignore_errors=True)
        self.extract_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [extractor, "x", "-y", f"-o{self.extract_dir}", str(part_paths[0])],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = f"{completed.stdout}\n{completed.stderr}"

        if completed.returncode == 0:
            files = self.extracted_files()
            if isinstance(files, DownloadError):
                shutil.rmtree(self.extract_dir, ignore_errors=True)
                return files
            if not files:
                shutil.rmtree(self.extract_dir, ignore_errors=True)
                return DownloadError(
                    "Multipart archive extracted no files.", retryable=False
                )
            return MultipartExtractionReady(group=self.group, files=files)

        lolg.warning(
            f"Multipart extraction failed for '{self.group}': {output.strip()}"
        )
        shutil.rmtree(self.extract_dir, ignore_errors=True)
        if _looks_permanent(output) and not _looks_incomplete(output):
            return DownloadError(
                "Multipart archive extraction failed.", retryable=False
            )
        return MultipartAccepted(group=self.group)

    def process_part(
        self, part: ArchivePart, url_key: str, file_path: Path
    ) -> MultipartProcessResult:
        metadata = self.load()

        if error := _metadata_error(metadata):
            file_path.unlink(missing_ok=True)
            return error

        if final_path := metadata.get("final_cache_path"):
            if (_cache_dir() / final_path).exists():
                file_path.unlink(missing_ok=True)
                return MultipartAlreadyComplete(self.group, final_path)
            self.reset()
            metadata = self.load()

        self.prune_missing_parts(metadata)

        if _is_finalizing(self.group):
            self.record_late_url_key(metadata, url_key)
            self.save(metadata)
            file_path.unlink(missing_ok=True)
            return MultipartAccepted(group=self.group)

        if error := self.store_part(metadata, part, url_key, file_path):
            if not error.retryable:
                metadata["error"] = error.to_cache()
                self.save(metadata)
            return error

        part_paths = self.contiguous_part_paths(metadata)
        if part_paths is None:
            self.save(metadata)
            return MultipartAccepted(group=self.group)

        result = _attempt_extraction(self.group, part_paths)
        if isinstance(result, MultipartExtractionReady):
            _set_finalizing(self.group)
        elif isinstance(result, DownloadError) and not result.retryable:
            metadata["error"] = result.to_cache()
        else:
            _clear_finalizing(self.group)
        self.save(metadata)
        return result

    def complete(self, final_cache_path: str) -> list[str]:
        metadata = self.load()
        url_keys = self.known_url_keys(metadata)
        _clear_finalizing(self.group)
        shutil.rmtree(self.parts_dir, ignore_errors=True)
        shutil.rmtree(self.extract_dir, ignore_errors=True)
        self.save(
            {
                "group": self.group,
                "final_cache_path": final_cache_path,
                "url_keys": url_keys,
            }
        )
        return url_keys


def get_final_cache_path(group: str) -> str | None:
    """Return the cache-relative final output path for a completed group."""
    with _lock_for_group(group):
        return MultipartJob(group).final_path()


def get_group_error(group: str) -> DownloadError | None:
    """Return a persisted terminal multipart group error, if one exists."""
    with _lock_for_group(group):
        return MultipartJob(group).error()


def has_url_key(group: str, url_key: str) -> bool:
    """Return whether a multipart cache entry is still backed by a stored part."""
    with _lock_for_group(group):
        return MultipartJob(group).has_url_key(url_key)


def _save_metadata(group: str, metadata: dict[str, Any]):
    MultipartJob(group).save(metadata)


def _extracted_files(group: str) -> list[Path] | DownloadError:
    return MultipartJob(group).extracted_files()


def _attempt_extraction(group: str, part_paths: list[Path]) -> MultipartProcessResult:
    return MultipartJob(group).attempt_extraction(part_paths)


def process_downloaded_file(
    url: str, url_key: str, file_path: Path
) -> MultipartProcessResult | None:
    """Store and possibly extract a supported archive part.

    Return None when multipart handling is disabled or the file is not a supported
    archive part.
    """
    if not is_enabled():
        return None
    part = detect_archive_part(file_path=file_path, url=url)
    if part is None:
        return None
    with _lock_for_group(part.group):
        return MultipartJob(part.group).process_part(part, url_key, file_path)


def complete_group(group: str, final_cache_path: str) -> list[str]:
    """Return known URL keys and keep only completed-group metadata."""
    with _lock_for_group(group):
        return MultipartJob(group).complete(final_cache_path)


def clear_finalizing(group: str):
    """Allow a group to retry finalization after cache move/zip failure."""
    with _lock_for_group(group):
        _clear_finalizing(group)
