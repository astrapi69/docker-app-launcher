"""Tests for the tray module - pure parts only, never starts a real tray."""

from __future__ import annotations

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
