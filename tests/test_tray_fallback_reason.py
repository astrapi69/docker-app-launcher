"""Why there is no tray icon must reach the user - except when it is by design (#107).

The taskbar fallback itself is correct. What was missing: the REASON only
ever existed in the debug log, so a user (and the next investigation) saw
a launcher that "just" behaved differently with no way to know why.

Narrowed after the tray decision: the frozen bundle ships without the
tray extra ON PURPOSE and its X closes the launcher (#108), so there is
nothing degraded for the user to notice - it returns no reason, and the
log must not call the extra "missing" either. Two causes remain, both
fixable by whoever hit them:

- a source install without the ``tray`` extra,
- a desktop that cannot dock an icon (no AppIndicator).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from docker_app_launcher import i18n, tray
from docker_app_launcher.config import LauncherConfig

REASON_KEYS = ("tray_missing_extra", "tray_no_desktop_support")
FRONTEND_MODULES = (
    "docker_app_launcher.frontends.tk_window",
    "docker_app_launcher.frontends.ctk_window",
    "docker_app_launcher.frontends.qt_window",
)


def _cfg(locale: str = "en") -> LauncherConfig:
    return LauncherConfig(
        app_name="Reason",
        container_name="reason",
        image_name="reason:test",
        compose_file="docker-compose.yml",
        locale=locale,
    )


class TestReasonPerCause:
    def test_source_install_without_the_extra_names_the_install_command(self, monkeypatch) -> None:
        monkeypatch.setattr(tray, "is_frozen", lambda: False)
        monkeypatch.setattr(tray, "HAS_TRAY", False)
        assert tray.background_fallback_reason() == "tray_missing_extra"
        assert "pip install" in i18n.t("tray_missing_extra", _cfg())

    def test_missing_desktop_support_names_the_extension(self, monkeypatch) -> None:
        monkeypatch.setattr(tray, "is_frozen", lambda: False)
        monkeypatch.setattr(tray, "HAS_TRAY", True)
        monkeypatch.setattr(tray, "_TRAY_BACKEND", "pystray._xorg")
        assert tray.background_fallback_reason() == "tray_no_desktop_support"
        assert "AppIndicator" in i18n.t("tray_no_desktop_support", _cfg())

    def test_a_working_tray_needs_no_explanation(self, monkeypatch) -> None:
        monkeypatch.setattr(tray, "is_frozen", lambda: False)
        monkeypatch.setattr(tray, "HAS_TRAY", True)
        monkeypatch.setattr(tray, "_TRAY_BACKEND", "appindicator")
        assert tray.background_fallback_reason() is None

    def test_the_frozen_bundle_reports_no_defect(self, monkeypatch) -> None:
        """By design, not degraded - and the X closes there anyway (#108)."""
        monkeypatch.setattr(tray, "is_frozen", lambda: True)
        monkeypatch.setattr(tray, "HAS_TRAY", False)
        assert tray.background_fallback_reason() is None


class TestTheLogDoesNotCallItMissing:
    def test_frozen_log_says_not_part_of_this_build(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(tray, "is_frozen", lambda: True)
        monkeypatch.setattr(tray, "HAS_TRAY", False)
        with caplog.at_level("DEBUG", logger="docker_app_launcher.tray"):
            tray.log_diagnostics(_cfg())
        text = caplog.text
        assert "not part of this build" in text
        assert "FAILED" not in text, (
            "the bundle deliberately ships without the extra - claiming a failure "
            "sends the next investigation after a defect that does not exist"
        )

    def test_source_log_still_names_the_import_failure(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(tray, "is_frozen", lambda: False)
        monkeypatch.setattr(tray, "HAS_TRAY", False)
        with caplog.at_level("DEBUG", logger="docker_app_launcher.tray"):
            tray.log_diagnostics(_cfg())
        assert "FAILED" in caplog.text


class TestVisibility:
    """A reason that never reaches the panel is not a fix."""

    @pytest.mark.parametrize("module_name", FRONTEND_MODULES)
    def test_every_frontend_logs_the_reason_on_the_fallback(self, module_name: str) -> None:
        module = __import__(module_name, fromlist=["*"])
        source = inspect.getsource(module)
        assert "background_fallback_reason()" in source, (
            f"{module_name} falls back to the taskbar without telling the user why"
        )

    def test_every_catalog_carries_both_keys(self) -> None:
        catalogs = sorted(Path("src/docker_app_launcher/i18n").glob("*.yaml"))
        assert len(catalogs) == 11
        missing = {
            c.stem: [k for k in REASON_KEYS if k not in yaml.safe_load(c.read_text(encoding="utf-8"))] for c in catalogs
        }
        gaps = {locale: keys for locale, keys in missing.items() if keys}
        assert not gaps, f"checked {len(catalogs)} catalogs for {list(REASON_KEYS)} - gaps: {gaps}"
