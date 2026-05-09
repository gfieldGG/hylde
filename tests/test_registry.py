"""Tests for hylde.registry module."""

from unittest.mock import MagicMock, patch

import pytest

from hylde import registry


class TestGetDownloaderForUrl:
    """Tests for get_downloader_for_url."""

    def test_matches_first_pattern(self):
        mock_mod = MagicMock()
        mock_mod.__name__ = "first"
        mock_mod2 = MagicMock()
        mock_mod2.__name__ = "second"

        with patch.object(
            registry,
            "DOWNLOADER_PATTERNS",
            [
                (r"example\.com", mock_mod),
                (r"foo\.com", mock_mod2),
            ],
        ):
            result = registry.get_downloader_for_url("https://example.com/page")

        assert result is mock_mod

    def test_raises_when_no_match(self):
        with patch.object(registry, "DOWNLOADER_PATTERNS", []):
            with pytest.raises(ValueError, match="No downloader matched"):
                registry.get_downloader_for_url("https://unknown.com")

    def test_uses_regex_search_not_match(self):
        mock_mod = MagicMock()
        mock_mod.__name__ = "mod"

        with patch.object(
            registry,
            "DOWNLOADER_PATTERNS",
            [
                (r"page", mock_mod),
            ],
        ):
            result = registry.get_downloader_for_url("https://example.com/page")

        assert result is mock_mod

    def test_returns_second_if_first_does_not_match(self):
        mock_mod1 = MagicMock()
        mock_mod1.__name__ = "first"
        mock_mod2 = MagicMock()
        mock_mod2.__name__ = "second"

        with patch.object(
            registry,
            "DOWNLOADER_PATTERNS",
            [
                (r"nope\.com", mock_mod1),
                (r"example\.com", mock_mod2),
            ],
        ):
            result = registry.get_downloader_for_url("https://example.com")

        assert result is mock_mod2

    def test_pattern_with_special_chars(self):
        mock_mod = MagicMock()
        mock_mod.__name__ = "mod"

        with patch.object(
            registry,
            "DOWNLOADER_PATTERNS",
            [
                (r"imgur\.com/\w+", mock_mod),
            ],
        ):
            result = registry.get_downloader_for_url("https://imgur.com/abc123")

        assert result is mock_mod

    def test_no_match_does_not_call_module(self):
        mock_mod = MagicMock()

        with patch.object(
            registry,
            "DOWNLOADER_PATTERNS",
            [
                (r"example\.com", mock_mod),
            ],
        ):
            with pytest.raises(ValueError):
                registry.get_downloader_for_url("https://other.com")

        mock_mod.assert_not_called()
