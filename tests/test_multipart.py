from pathlib import Path
from unittest.mock import patch

from hylde import multipart
from hylde.result import DownloadError, MultipartAccepted


def test_detects_zip_numbered_part(tmp_path: Path):
    file_path = tmp_path / "Example.zip.001"
    file_path.write_text("data")

    part = multipart.detect_archive_part(file_path, "https://bunkr.cr/f/abc")

    assert part is not None
    assert part.archive_name == "Example.zip"
    assert part.part_number == 1
    assert part.host == "bunkr.cr"


def test_detects_rar_part(tmp_path: Path):
    file_path = tmp_path / "Example.part12.rar"
    file_path.write_text("data")

    part = multipart.detect_archive_part(file_path, "https://example.com/file")

    assert part is not None
    assert part.archive_name == "Example.rar"
    assert part.part_number == 12


def test_ignores_unsupported_part_name(tmp_path: Path):
    file_path = tmp_path / "Example.7z.001"
    file_path.write_text("data")

    assert multipart.detect_archive_part(file_path, "https://example.com/file") is None


def test_stores_downloaded_part_and_returns_accepted(tmp_path: Path):
    file_path = tmp_path / "Archive.zip.001"
    file_path.write_text("partial")

    with (
        patch("hylde.multipart._cache_dir", return_value=tmp_path / "cache"),
        patch(
            "hylde.multipart._attempt_extraction", return_value=MultipartAccepted("g")
        ) as extract,
    ):
        result = multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey", file_path
        )

    assert isinstance(result, MultipartAccepted)
    assert not file_path.exists()
    assert extract.called


def test_out_of_order_part_is_stored_without_extraction(tmp_path: Path):
    file_path = tmp_path / "Archive.zip.002"
    file_path.write_text("partial")

    with (
        patch("hylde.multipart._cache_dir", return_value=tmp_path / "cache"),
        patch("hylde.multipart._attempt_extraction") as extract,
    ):
        result = multipart.process_downloaded_file(
            "https://example.com/f/two", "urlkey", file_path
        )

    assert isinstance(result, MultipartAccepted)
    assert not file_path.exists()
    extract.assert_not_called()


def test_finalizing_group_accepts_later_part_without_extraction(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    first_part = tmp_path / "Archive.zip.001"
    first_part.write_text("first")
    part = multipart.detect_archive_part(first_part, "https://example.com/f/one")
    assert part is not None

    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch(
            "hylde.multipart._attempt_extraction",
            return_value=multipart.MultipartExtractionReady(
                part.group, [tmp_path / "out"]
            ),
        ),
    ):
        ready = multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", first_part
        )

    assert isinstance(ready, multipart.MultipartExtractionReady)

    second_part = tmp_path / "Archive.zip.002"
    second_part.write_text("second")
    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch("hylde.multipart._attempt_extraction") as extract,
    ):
        result = multipart.process_downloaded_file(
            "https://example.com/f/two", "urlkey2", second_part
        )

    assert isinstance(result, MultipartAccepted)
    assert not second_part.exists()
    extract.assert_not_called()

    with patch("hylde.multipart._cache_dir", return_value=cache_dir):
        url_keys = multipart.complete_group(ready.group, "final/output.bin")

    assert url_keys == ["urlkey1", "urlkey2"]


def test_complete_group_returns_url_keys_and_deletes_workdir(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    file_path = tmp_path / "Archive.zip.001"
    file_path.write_text("partial")
    part = multipart.detect_archive_part(file_path, "https://example.com/f/one")
    assert part is not None

    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch(
            "hylde.multipart._attempt_extraction",
            return_value=MultipartAccepted(part.group),
        ),
    ):
        multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", file_path
        )
        final_path = cache_dir / "final" / "output.bin"
        final_path.parent.mkdir(parents=True)
        final_path.write_text("done")
        url_keys = multipart.complete_group(part.group, "final/output.bin")

    assert url_keys == ["urlkey1"]
    assert not (cache_dir / "_multipart" / part.group / "parts").exists()
    assert not (cache_dir / "_multipart" / part.group / "extract").exists()
    with patch("hylde.multipart._cache_dir", return_value=cache_dir):
        assert multipart.get_final_cache_path(part.group) == "final/output.bin"


def test_completed_group_returns_existing_final_for_late_part(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    file_path = tmp_path / "Archive.zip.001"
    file_path.write_text("partial")
    part = multipart.detect_archive_part(file_path, "https://example.com/f/one")
    assert part is not None

    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch(
            "hylde.multipart._attempt_extraction",
            return_value=MultipartAccepted(part.group),
        ),
    ):
        multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", file_path
        )
        final_path = cache_dir / "final" / "output.bin"
        final_path.parent.mkdir(parents=True)
        final_path.write_text("done")
        multipart.complete_group(part.group, "final/output.bin")

        late_part = tmp_path / "Archive.zip.002"
        late_part.write_text("late")
        result = multipart.process_downloaded_file(
            "https://example.com/f/two", "urlkey2", late_part
        )

    assert result == multipart.MultipartAlreadyComplete(
        group=part.group, final_cache_path="final/output.bin"
    )
    assert not late_part.exists()


def test_missing_completed_output_clears_tombstone_for_rebuild(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    file_path = tmp_path / "Archive.zip.001"
    file_path.write_text("partial")
    part = multipart.detect_archive_part(file_path, "https://example.com/f/one")
    assert part is not None

    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch(
            "hylde.multipart._attempt_extraction",
            return_value=MultipartAccepted(part.group),
        ),
    ):
        multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", file_path
        )
        multipart.complete_group(part.group, "final/missing.bin")
        assert multipart.get_final_cache_path(part.group) is None
        assert not (cache_dir / "_multipart" / part.group).exists()

        replacement = tmp_path / "replacement" / "Archive.zip.001"
        replacement.parent.mkdir()
        replacement.write_text("partial")
        result = multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", replacement
        )

    assert isinstance(result, MultipartAccepted)
    assert not replacement.exists()


def test_missing_recorded_part_file_clears_group_state(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    group = "group"
    with patch("hylde.multipart._cache_dir", return_value=cache_dir):
        multipart._save_metadata(
            group,
            {
                "group": group,
                "parts": {
                    "1": {
                        "filename": "archive.zip.001",
                        "size": 1,
                        "url_keys": ["urlkey"],
                    }
                },
            },
        )

        assert multipart.has_group_state(group) is False
        assert not (cache_dir / "_multipart" / group).exists()


def test_permanent_extraction_error_is_persisted_for_group(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    file_path = tmp_path / "Archive.zip.001"
    file_path.write_text("partial")
    part = multipart.detect_archive_part(file_path, "https://example.com/f/one")
    assert part is not None

    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch(
            "hylde.multipart._attempt_extraction",
            return_value=DownloadError("Archive is encrypted.", retryable=False),
        ),
    ):
        result = multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", file_path
        )
        group_error = multipart.get_group_error(part.group)

    assert isinstance(result, DownloadError)
    assert group_error == DownloadError("Archive is encrypted.", retryable=False)


def test_part_collision_error_is_persisted_for_group(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    first_part = tmp_path / "one" / "Archive.zip.001"
    second_part = tmp_path / "two" / "Archive.zip.001"
    first_part.parent.mkdir()
    second_part.parent.mkdir()
    first_part.write_text("one")
    second_part.write_text("different")
    part = multipart.detect_archive_part(first_part, "https://example.com/f/one")
    assert part is not None

    with (
        patch("hylde.multipart._cache_dir", return_value=cache_dir),
        patch(
            "hylde.multipart._attempt_extraction",
            return_value=MultipartAccepted(part.group),
        ),
    ):
        multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", first_part
        )
        result = multipart.process_downloaded_file(
            "https://example.com/f/two", "urlkey2", second_part
        )
        group_error = multipart.get_group_error(part.group)

    assert result == DownloadError("Multipart archive part collision.", retryable=False)
    assert group_error == result
    assert not second_part.exists()


def test_existing_group_error_short_circuits_new_part(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    file_path = tmp_path / "Archive.zip.001"
    file_path.write_text("partial")
    part = multipart.detect_archive_part(file_path, "https://example.com/f/one")
    assert part is not None

    with patch("hylde.multipart._cache_dir", return_value=cache_dir):
        metadata = {
            "group": part.group,
            "parts": {},
            "error": DownloadError("Archive failed.", retryable=False).to_cache(),
        }
        multipart._save_metadata(part.group, metadata)
        result = multipart.process_downloaded_file(
            "https://example.com/f/one", "urlkey1", file_path
        )

    assert result == DownloadError("Archive failed.", retryable=False)
    assert not file_path.exists()


def test_extracted_files_rejects_symlinks(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    extract_dir = cache_dir / "_multipart" / "group" / "extract"
    extract_dir.mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("secret")
    (extract_dir / "unsafe.txt").symlink_to(target)

    with patch("hylde.multipart._cache_dir", return_value=cache_dir):
        result = multipart._extracted_files("group")

    assert isinstance(result, DownloadError)
    assert result.retryable is False
