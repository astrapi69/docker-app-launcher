"""Tests for the tray module - pure parts only, never starts a real tray."""

from __future__ import annotations

from typing import Any

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
    assert labels["open"] == "Öffnen"
    assert labels["quit"] == "Beenden"


def test_load_icon_image_missing_path_returns_none() -> None:
    assert tray._load_icon_image("") is None


def test_load_icon_image_nonexistent_returns_none() -> None:
    assert tray._load_icon_image("/no/such/icon.png") is None


def test_controller_start_without_image_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the tray extra installed a missing icon_path no longer aborts start()
    # (a default icon is generated), so simulate icon resolution failing: start()
    # must bail out cleanly without ever creating a real tray icon.
    monkeypatch.setattr(tray, "_resolve_tray_image", lambda config: None)
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


# --- TrayController runtime with a fake backend (never a real tray) ---------


class _FakeTrayIcon:
    """Deterministic pystray.Icon stand-in: run() invokes setup synchronously."""

    __module__ = "tests.fake_backend"

    def __init__(self, name, image, tooltip, menu) -> None:
        self.name = name
        self.image = image
        self.tooltip = tooltip
        self.menu = menu
        self.visible = False
        self.stopped = False

    def run(self, setup) -> None:
        setup(self)

    def stop(self) -> None:
        self.stopped = True


def _controller(**kwargs: Any) -> tray.TrayController:
    defaults: dict[str, Any] = {"config": _cfg(), "port": 8080, "labels": {}, "callbacks": {}}
    defaults.update(kwargs)
    return tray.TrayController(**defaults)


class _FakeMenuItem:
    def __init__(self, label, handler, default=False):
        self.label = label
        self.handler = handler
        self.default = default


class _FakeMenu:
    def __init__(self, *items):
        self.items = items


@pytest.fixture
def fake_backend(monkeypatch):
    """Force a fully fake tray stack: no pystray import, no real icon.

    Also fakes ``tray.pystray`` (MenuItem/Menu): on a box where importing
    pystray fails (headless CI), the module attribute is ``None`` - the
    controller tests must never depend on the real library.
    """
    import types

    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray, "_TrayIcon", _FakeTrayIcon)
    monkeypatch.setattr(tray, "_resolve_tray_image", lambda config: object())
    monkeypatch.setattr(tray, "pystray", types.SimpleNamespace(MenuItem=_FakeMenuItem, Menu=_FakeMenu))
    return _FakeTrayIcon


class TestControllerStart:
    def test_start_success_shows_icon(self, fake_backend) -> None:
        controller = _controller()
        assert controller.start() is True
        assert controller._icon.visible is True

    def test_start_false_when_extra_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(tray, "HAS_TRAY", False)
        assert _controller().start() is False

    def test_start_false_on_unreliable_backend(self, fake_backend, monkeypatch) -> None:
        monkeypatch.setattr(fake_backend, "__module__", "pystray._xorg")
        assert _controller().start() is False

    def test_start_false_when_setup_never_fires(self, fake_backend, monkeypatch) -> None:
        # A backend whose run() blocks without calling setup -> ready times out,
        # start() must give up, clean up, and report False.
        monkeypatch.setattr(_FakeTrayIcon, "run", lambda self, setup: None)
        monkeypatch.setattr(tray.TrayController, "_READY_TIMEOUT_SECONDS", 0.05)
        controller = _controller()
        assert controller.start() is False
        assert controller._icon is None

    def test_start_false_when_run_loop_raises(self, fake_backend, monkeypatch) -> None:
        def broken_run(self, setup):
            raise RuntimeError("backend exploded")

        monkeypatch.setattr(_FakeTrayIcon, "run", broken_run)
        monkeypatch.setattr(tray.TrayController, "_READY_TIMEOUT_SECONDS", 0.5)
        assert _controller().start() is False

    def test_icon_gets_slug_and_menu(self, fake_backend, monkeypatch) -> None:
        class _Item:
            def __init__(self, label, handler, default=False):
                self.label = label
                self.default = default

        class _Menu:
            def __init__(self, *items):
                self.items = items

        import types

        monkeypatch.setattr(tray, "pystray", types.SimpleNamespace(MenuItem=_Item, Menu=_Menu))
        calls: list[str] = []
        controller = _controller(
            labels={"open": "Open!", "stop": "Stop!"},
            callbacks={"open": lambda: calls.append("open"), "stop": lambda: calls.append("stop")},
        )
        assert controller.start() is True
        icon = controller._icon
        assert icon.name  # app_slug or "launcher"
        labels = [item.label for item in icon.menu.items]
        assert labels == ["Open!", "Stop!"]  # only supplied callbacks, display order
        assert [item.default for item in icon.menu.items] == [True, False]  # "open" is default


class TestControllerStop:
    def test_stop_stops_icon_and_clears_state(self, fake_backend) -> None:
        controller = _controller()
        assert controller.start() is True
        icon = controller._icon
        controller.stop()
        assert icon.stopped is True
        assert controller._icon is None and controller._thread is None

    def test_stop_swallows_backend_errors(self, fake_backend, monkeypatch) -> None:
        controller = _controller()
        assert controller.start() is True

        def broken_stop(self):
            raise RuntimeError("cannot stop")

        monkeypatch.setattr(_FakeTrayIcon, "stop", broken_stop)
        controller.stop()  # must not raise
        assert controller._icon is None


class TestLogDiagnostics:
    def test_with_tray_and_missing_icon_path(self, caplog) -> None:
        import logging

        cfg = _cfg()
        with caplog.at_level(logging.DEBUG, logger="docker_app_launcher.tray"):
            tray.log_diagnostics(cfg)
        assert any("Tray:" in message for message in caplog.messages)

    def test_without_tray(self, monkeypatch, caplog) -> None:
        import logging

        monkeypatch.setattr(tray, "HAS_TRAY", False)
        cfg = _cfg()
        with caplog.at_level(logging.DEBUG, logger="docker_app_launcher.tray"):
            tray.log_diagnostics(cfg)
        assert any("FAILED" in message or "import" in message for message in caplog.messages)

    def test_icon_path_found_vs_missing(self, tmp_path, caplog) -> None:
        import logging

        icon = tmp_path / "icon.png"
        icon.write_bytes(b"png")
        cfg = LauncherConfig(app_name="Demo", icon_path=str(icon)).resolve()
        with caplog.at_level(logging.DEBUG, logger="docker_app_launcher.tray"):
            tray.log_diagnostics(cfg)
        assert any("found" in message for message in caplog.messages)
