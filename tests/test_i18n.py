"""Tests for the i18n layer: lookup, fallback, interpolation, overrides, parity."""

from __future__ import annotations

from docker_app_launcher import i18n
from docker_app_launcher.config import LauncherConfig


def _cfg(locale: str = "en", **kw) -> LauncherConfig:
    return LauncherConfig(app_name="Demo", locale=locale, **kw).resolve()


class TestTranslate:
    def test_english(self) -> None:
        assert i18n.t("install", _cfg("en")) == "Install"

    def test_german(self) -> None:
        assert i18n.t("install", _cfg("de")) == "Installieren"

    def test_app_interpolation(self) -> None:
        assert i18n.t("not_installed", _cfg("en")) == "Demo is not installed."

    def test_kwarg_interpolation(self) -> None:
        assert i18n.t("running", _cfg("en"), port=8080) == "Demo is running on port 8080."

    def test_unknown_locale_falls_back_to_english(self) -> None:
        assert i18n.t("install", _cfg("xx")) == "Install"

    def test_unknown_key_returns_key(self) -> None:
        assert i18n.t("totally_missing_key", _cfg("en")) == "totally_missing_key"

    def test_missing_format_arg_returns_template(self) -> None:
        # "running" needs {port}; omitting it should not raise.
        result = i18n.t("running", _cfg("en"))
        assert "{port}" in result


class TestCustomStrings:
    def test_override_wins(self) -> None:
        cfg = _cfg("en", custom_strings={"en": {"install": "Set up now"}})
        assert i18n.t("install", cfg) == "Set up now"

    def test_override_interpolates(self) -> None:
        cfg = _cfg("en", custom_strings={"en": {"ready": "{app} good to go"}})
        assert i18n.t("ready", cfg) == "Demo good to go"

    def test_override_only_for_its_locale(self) -> None:
        cfg = _cfg("de", custom_strings={"en": {"install": "X"}})
        assert i18n.t("install", cfg) == "Installieren"


class TestParity:
    def test_de_has_every_en_key(self) -> None:
        missing = set(i18n.STRINGS["en"]) - set(i18n.STRINGS["de"])
        assert missing == set(), f"German catalog missing keys: {sorted(missing)}"

    def test_en_has_every_de_key(self) -> None:
        missing = set(i18n.STRINGS["de"]) - set(i18n.STRINGS["en"])
        assert missing == set(), f"English catalog missing keys: {sorted(missing)}"

    def test_available_languages(self) -> None:
        assert i18n.available_languages() == ["de", "en"]
