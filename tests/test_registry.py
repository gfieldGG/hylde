"""Tests for hylde.registry module."""

from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from hylde import registry


class TestLoadDownloader:
    def test_loads_builtin_downloader(self):
        result = registry.load_downloader("gallerydl")

        assert result.__name__ == "hylde.downloaders.gallerydl"

    def test_loads_external_downloader_from_file(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text(
            "def download_url(url, url_key):\n"
            "    return []\n"
        )

        with patch.object(registry, "_plugin_modules", return_value={"plugin": str(plugin)}):
            result = registry.load_downloader("plugin")

        assert result.download_url("https://example.com", "key") == []

    def test_loads_external_downloader_from_dotted_module(self):
        module = ModuleType("custom_downloader")
        module.download_url = lambda url, url_key: []

        with patch.dict("sys.modules", {"custom_downloader": module}), patch.object(
            registry, "_plugin_modules", return_value={"custom": "custom_downloader"}
        ):
            result = registry.load_downloader("custom")

        assert result is module

    def test_external_downloader_must_define_download_url(self, tmp_path):
        plugin = tmp_path / "plugin.py"
        plugin.write_text("VALUE = 1\n")

        with patch.object(registry, "_plugin_modules", return_value={"plugin": str(plugin)}):
            with pytest.raises(ValueError, match="download_url"):
                registry.load_downloader("plugin")

    def test_missing_downloader_raises_module_not_found_error(self):
        with patch.object(registry, "_plugin_modules", return_value={}):
            with pytest.raises(ModuleNotFoundError):
                registry.load_downloader("missing")


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
