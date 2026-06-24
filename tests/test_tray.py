"""Tests for the tray module - pure parts only, never starts a real tray."""

from __future__ import annotations

import pytest

from docker_app_launcher import tray
from docker_app_launcher.config import LauncherConfig


def _cfg(locale: str = "en") -> LauncherConfig:
    return LauncherConfig(app_name="Demo", locale=locale).resolve()


def test_tray_available_returns_bool() -> None:
    assert isinstance(tray.tray_available(), bool)


def test_menu_action_ids_order() -> None:
    assert tray.menu_action_ids() == ["open", "open_browser", "stop", "quit"]


def test_menu_labels_localized_en() -> None:
    labels = tray.menu_labels(_cfg("en"))
    assert labels["open"] == "Open"
    assert labels["quit"] == "Quit"
    assert labels["stop"] == "Stop"


def test_menu_labels_localized_de() -> None:
    labels = tray.menu_labels(_cfg("de"))
    assert labels["open"] == "Oeffnen"
    assert labels["quit"] == "Beenden"


def test_load_icon_image_missing_path_returns_none() -> None:
    assert tray._load_icon_image("") is None


def test_load_icon_image_nonexistent_returns_none() -> None:
    assert tray._load_icon_image("/no/such/icon.png") is None


def test_controller_start_without_icon_returns_false() -> None:
    # No icon_path configured -> start() bails out cleanly (no tray shown).
    controller = tray.TrayController(config=_cfg(), port=8080, labels={}, callbacks={})
    assert controller.start() is False


def test_controller_stop_is_safe_when_not_started() -> None:
    controller = tray.TrayController(config=_cfg(), port=8080, labels={}, callbacks={})
    controller.stop()  # must not raise


def test_as_menu_handler_calls_callback() -> None:
    calls: list[int] = []
    handler = tray._as_menu_handler(lambda: calls.append(1))
    handler(None, None)
    assert calls == [1]


class _FakeRoot:
    """Stand-in Tk window recording which background action was taken."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def withdraw(self) -> None:
        self.calls.append("withdraw")

    def iconify(self) -> None:
        self.calls.append("iconify")


class _FakeController:
    def __init__(self, *, started: bool) -> None:
        self._started = started

    def start(self) -> bool:
        return self._started


class TestTryMinimizeToBackground:
    def test_tray_success_withdraws(self) -> None:
        root = _FakeRoot()
        mode = tray.try_minimize_to_background(root, _FakeController(started=True))
        assert mode == "tray"
        assert root.calls == ["withdraw"]

    def test_tray_failure_iconifies(self) -> None:
        # Tray present but start() failed (e.g. no AppIndicator) -> taskbar.
        root = _FakeRoot()
        mode = tray.try_minimize_to_background(root, _FakeController(started=False))
        assert mode == "iconify"
        assert root.calls == ["iconify"]

    def test_no_controller_iconifies(self) -> None:
        root = _FakeRoot()
        mode = tray.try_minimize_to_background(root, None)
        assert mode == "iconify"
        assert root.calls == ["iconify"]


class TestTrayImageResolution:
    """tray_icon_path -> icon_path -> generated default (#9 follow-up)."""

    def _patch(self, monkeypatch) -> None:
        monkeypatch.setattr(tray, "HAS_TRAY", True)
        monkeypatch.setattr(tray, "_load_icon_image", lambda p: f"img:{p}" if p else None)
        monkeypatch.setattr(tray, "_generate_default_icon", lambda name: f"default:{name}")

    def test_prefers_tray_icon_path(self, monkeypatch) -> None:
        self._patch(monkeypatch)
        cfg = LauncherConfig(app_name="X", icon_path="win.png", tray_icon_path="tray.png").resolve()
        assert tray._resolve_tray_image(cfg) == "img:tray.png"

    def test_falls_back_to_icon_path(self, monkeypatch) -> None:
        self._patch(monkeypatch)
        cfg = LauncherConfig(app_name="X", icon_path="win.png").resolve()
        assert tray._resolve_tray_image(cfg) == "img:win.png"

    def test_generates_default_when_no_paths(self, monkeypatch) -> None:
        self._patch(monkeypatch)
        cfg = LauncherConfig(app_name="Demo").resolve()
        assert tray._resolve_tray_image(cfg) == "default:Demo"

    def test_none_without_tray_extra(self, monkeypatch) -> None:
        monkeypatch.setattr(tray, "HAS_TRAY", False)
        cfg = LauncherConfig(app_name="X", icon_path="win.png").resolve()
        assert tray._resolve_tray_image(cfg) is None


def _pillow_available() -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _pillow_available(), reason="Pillow (the tray extra) not installed")
def test_generate_default_icon_renders(monkeypatch) -> None:
    import PIL.Image

    # Run independently of pystray: force the tray-present guard and the real
    # PIL.Image even if importing pystray nulled the module-level Image.
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray, "Image", PIL.Image)
    img = tray._generate_default_icon("Demo", size=64)
    assert img is not None
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_generate_default_icon_none_without_tray(monkeypatch) -> None:
    monkeypatch.setattr(tray, "HAS_TRAY", False)
    assert tray._generate_default_icon("Demo") is None


def test_log_diagnostics_never_raises() -> None:
    # Diagnostics must be safe with and without a configured icon, tray present
    # or not (it only logs).
    tray.log_diagnostics(_cfg())
    tray.log_diagnostics(LauncherConfig(app_name="X", icon_path="/no/such.png").resolve())
