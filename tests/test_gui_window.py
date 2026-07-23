"""Real-window GUI tests: drive ``LauncherApp`` through Tk's own event layer.

No OS-level automation for the ASSERTIONS (``invoke()`` / ``event_generate``
are deterministic); ``pyautogui`` is used only for best-effort SCREENSHOTS of
each state so a human can review the window visually. All ``actions`` calls
are mocked - no Docker, no network.

Needs a display (real, XWayland, or ``xvfb-run``); every test skips cleanly
when Tk cannot open one. Screenshots are written only when the
``DAL_SCREENSHOTS`` env var is set (see ``make screenshots``) and silently
skipped when pyautogui cannot reach the display (e.g. pure Wayland).
"""

from __future__ import annotations

import os
import threading as _threading
import tkinter as tk
from pathlib import Path

import pytest

from docker_app_launcher import actions, gui, tray
from docker_app_launcher.config import LauncherConfig


def _display_available() -> bool:
    try:
        probe = tk.Tk()
    except tk.TclError:
        return False
    probe.destroy()
    return True


pytestmark = pytest.mark.skipif(not _display_available(), reason="no display (run under xvfb-run)")

SCREENSHOT_DIR = Path(os.environ.get("DAL_SCREENSHOT_DIR", "test-screenshots"))


def _screenshot(app: gui.LauncherApp, name: str) -> None:
    """Best-effort window screenshot via pyautogui; never fails the test."""
    if not os.environ.get("DAL_SCREENSHOTS"):
        return
    try:
        import pyautogui

        app.update_idletasks()
        app.update()
        region = (app.winfo_rootx(), app.winfo_rooty(), app.winfo_width(), app.winfo_height())
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        pyautogui.screenshot(str(SCREENSHOT_DIR / f"{name}.png"), region=region)
    except Exception as exc:  # noqa: BLE001 - screenshots are documentation, not assertions
        print(f"screenshot {name} skipped: {exc}")


@pytest.fixture
def gui_state(monkeypatch):
    """Mock every action the window calls; the dict controls the app state."""
    state = {"value": "not_installed"}
    monkeypatch.setattr(actions, "get_state", lambda c: state["value"])
    monkeypatch.setattr(
        actions,
        "check_docker_detailed",
        lambda c: {
            "status": "daemon_stopped",
            "detail": "daemon not running",
            "command": "systemctl start docker",
            "can_start": True,
            "installed": True,
        },
    )
    monkeypatch.setattr(actions, "check_port", lambda p: (True, ""))
    monkeypatch.setattr(actions, "resolve_port", lambda c: c.default_port)
    monkeypatch.setattr(actions, "resolve_locale", lambda c: c.locale if c.locale != "auto" else "en")
    monkeypatch.setattr(actions, "set_locale", lambda c, code: code)
    monkeypatch.setattr(actions, "find_stale_artifacts", lambda c: {})
    return state


@pytest.fixture
def app(gui_state):
    config = LauncherConfig(
        app_name="Test App",
        default_port=8080,
        locale="en",
        cleanup_on_start=False,
        update_check_enabled=False,
    )
    window = gui.LauncherApp(config)
    window.update()
    yield window
    window.destroy()


class TestWindowConstruction:
    def test_window_builds_with_title(self, app) -> None:
        assert app.title() == "Test App"
        _screenshot(app, "not_installed_en")

    def test_all_buttons_exist_and_visible(self, app) -> None:
        assert set(app._buttons) == set(gui.PRIMARY_BUTTONS) | set(gui.SECONDARY_BUTTONS)
        for name, btn in app._buttons.items():
            assert btn.winfo_manager(), f"button {name} is not placed"

    def test_port_field_prefilled(self, app) -> None:
        assert app._port_var.get() == "8080"


class TestStateRendering:
    def _set_state(self, app, gui_state, value: str) -> None:
        gui_state["value"] = value
        app._refresh()
        app.update()

    def test_not_installed_enables_install_only_actions(self, app, gui_state) -> None:
        self._set_state(app, gui_state, "not_installed")
        assert app._buttons["install"]["state"] == "normal"
        assert app._buttons["stop"]["state"] == "disabled"
        assert app._buttons["cleanup"]["state"] == "normal"

    def test_running_enables_stop_and_open(self, app, gui_state) -> None:
        self._set_state(app, gui_state, "running")
        assert app._buttons["stop"]["state"] == "normal"
        assert app._buttons["open_browser"]["state"] == "normal"
        assert app._buttons["install"]["state"] == "disabled"
        _screenshot(app, "running_en")

    def test_stopped_enables_start(self, app, gui_state) -> None:
        self._set_state(app, gui_state, "stopped")
        assert app._buttons["start"]["state"] == "normal"
        assert app._buttons["stop"]["state"] == "disabled"
        _screenshot(app, "stopped_en")

    def test_no_docker_disables_all_and_shows_help(self, app, gui_state) -> None:
        self._set_state(app, gui_state, "no_docker")
        for name in ("install", "start", "stop", "open_browser", "uninstall", "cleanup"):
            assert app._buttons[name]["state"] == "disabled", f"{name} must be disabled without docker"
        assert app._docker_help_frame.winfo_manager(), "docker help panel must be packed"
        _screenshot(app, "no_docker_en")

    def test_port_editable_only_in_editable_states(self, app, gui_state) -> None:
        self._set_state(app, gui_state, "running")
        running_state = str(app._port_entry["state"])
        self._set_state(app, gui_state, "no_docker")
        assert str(app._port_entry["state"]) == "disabled"
        assert running_state == "normal"


class TestLanguageSwitch:
    def test_switch_to_german_relabels_buttons(self, app, gui_state) -> None:
        from docker_app_launcher.config import LOCALE_LABELS

        app._locale_var.set(LOCALE_LABELS["de"])
        app._on_locale_change()
        app.update()
        assert app._cfg.locale == "de"
        labels = [app._buttons[name]["text"] for name in gui.PRIMARY_BUTTONS]
        assert any("Installieren" in label or "installieren" in label for label in labels)
        _screenshot(app, "not_installed_de")

    def test_same_language_is_noop(self, app, gui_state) -> None:
        from docker_app_launcher.config import LOCALE_LABELS

        before = app._buttons["install"]["text"]
        app._locale_var.set(LOCALE_LABELS["en"])
        app._on_locale_change()
        assert app._buttons["install"]["text"] == before


class TestLogAndClipboard:
    def test_log_appends_lines(self, app) -> None:
        app._log("hello from the test")
        content = app._status.get("1.0", "end")
        assert "hello from the test" in content

    def test_clear_status_empties_log(self, app) -> None:
        app._log("something")
        app._clear_status()
        assert app._status.get("1.0", "end").strip() == ""

    def test_copy_log_puts_content_on_clipboard(self, app) -> None:
        app._log("copy me")
        app._copy_log()
        app.update()
        assert "copy me" in app.clipboard_get()

    def test_copy_log_flips_button_label(self, app) -> None:
        app._log("something")
        original = app._copy_log_btn["text"]
        app._copy_log()
        assert app._copy_log_btn["text"] != original  # localized "Copied!"

    def test_copy_empty_log_is_noop(self, app) -> None:
        original = app._copy_log_btn["text"]
        app._copy_log()
        assert app._copy_log_btn["text"] == original


class TestPortValidation:
    def test_invalid_port_shows_cross(self, app) -> None:
        app._port_var.set("not-a-port")
        app._validate_port()
        assert app._port_indicator["text"] == "✗"

    def test_free_port_shows_check(self, app) -> None:
        app._port_var.set("8080")
        app._validate_port()
        assert app._port_indicator["text"] == "✓"

    def test_taken_port_shows_cross(self, app, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_port", lambda p: (False, "in use"))
        app._port_var.set("8080")
        app._validate_port()
        assert app._port_indicator["text"] == "✗"


@pytest.mark.parametrize("locale", ["en", "de", "el", "es", "fr", "hi", "ja", "ko", "pt", "tr", "id"])
def test_screenshot_every_language(gui_state, locale) -> None:
    """One window per language: builds, renders, and (optionally) documents it."""
    config = LauncherConfig(
        app_name="Test App",
        default_port=8080,
        locale=locale,
        cleanup_on_start=False,
        update_check_enabled=False,
    )
    window = gui.LauncherApp(config)
    try:
        window.update()
        assert window._buttons["install"]["text"], "button label must not be empty"
        _screenshot(window, f"not_installed_{locale}")
    finally:
        window.destroy()


class _InlineThread:
    """threading.Thread stand-in: start() runs the target synchronously."""

    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


@pytest.fixture
def inline_threads(monkeypatch):
    """Make every gui-spawned worker run synchronously on the Tk thread."""
    monkeypatch.setattr(_threading, "Thread", _InlineThread)


class TestActionFlow:
    def test_action_success_logs_and_reenables(self, app, gui_state, inline_threads, monkeypatch) -> None:
        monkeypatch.setattr(gui, "dispatch_action", lambda action_id, cfg, **k: (True, "install done"))
        gui_state["value"] = "not_installed"
        app._buttons["install"].invoke()
        app.update()  # flush the after() callbacks (result + refresh)
        assert "install done" in app._status.get("1.0", "end")
        assert app._buttons["install"]["state"] == "normal"  # busy released

    def test_action_failure_logs_error_and_survives_broken_hook(
        self, app, gui_state, inline_threads, monkeypatch
    ) -> None:
        def broken_hook(cfg, msg):
            raise RuntimeError("hook exploded")

        app._cfg.on_error = broken_hook
        monkeypatch.setattr(gui, "dispatch_action", lambda action_id, cfg, **k: (False, "install blew up"))
        app._buttons["install"].invoke()
        app.update()
        assert "install blew up" in app._status.get("1.0", "end")

    def test_install_persists_typed_port(self, app, gui_state, inline_threads, monkeypatch) -> None:
        persisted: list[int] = []

        def record_port(c, p):
            persisted.append(p)
            return p

        monkeypatch.setattr(actions, "set_port", record_port)
        monkeypatch.setattr(gui, "dispatch_action", lambda action_id, cfg, **k: (True, "ok"))
        app._port_var.set("9999")
        app._on_action("install")
        app.update()
        assert persisted == [9999]

    def test_stop_never_persists_the_port(self, app, gui_state, inline_threads, monkeypatch) -> None:
        persisted: list[int] = []

        def record_port(c, p):
            persisted.append(p)
            return p

        monkeypatch.setattr(actions, "set_port", record_port)
        monkeypatch.setattr(gui, "dispatch_action", lambda action_id, cfg, **k: (True, "ok"))
        app._port_var.set("9999")
        app._on_action("stop")
        app.update()
        assert persisted == []

    def test_busy_disables_every_button_in_the_window(self, app) -> None:
        app._set_busy(True)
        assert all(str(btn["state"]) == "disabled" for btn in app._iter_buttons())
        app._set_busy(False)
        assert all(str(btn["state"]) == "normal" for btn in app._iter_buttons())


class TestCleanupOffer:
    def test_offer_renders_run_and_skip_buttons(self, app) -> None:
        before = len(app._iter_buttons())
        app._show_cleanup_offer({"containers": ["old-app"]})
        app.update()
        assert len(app._iter_buttons()) == before + 2
        _screenshot(app, "cleanup_offer_en")

    def test_skip_removes_offer_and_logs(self, app) -> None:
        app._show_cleanup_offer({"containers": ["old-app"]})
        app.update()
        buttons = [b for b in app._iter_buttons() if b["text"] == app._t("skip")]
        assert buttons, "skip button must exist"
        count_before = len(app._iter_buttons())
        buttons[0].invoke()
        app.update()
        assert len(app._iter_buttons()) == count_before - 2
        assert app._t("cleanup_skipped") in app._status.get("1.0", "end")

    def test_manual_cleanup_reports_nothing_found(self, app, inline_threads, monkeypatch) -> None:
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda c: {})
        app._run_manual_cleanup()
        app.update()
        assert app._t("cleanup_none") in app._status.get("1.0", "end")

    def test_manual_cleanup_scan_error_is_reported(self, app, inline_threads, monkeypatch) -> None:
        def boom(c):
            raise RuntimeError("scan failed hard")

        monkeypatch.setattr(actions, "find_stale_artifacts", boom)
        app._run_manual_cleanup()
        app.update()
        assert "scan failed hard" in app._status.get("1.0", "end")

    def test_offer_run_invokes_cleanup_stale(self, app, gui_state, inline_threads, monkeypatch) -> None:
        ran: list[dict[str, list[object]]] = []

        def fake_cleanup(cfg, stale, **k):
            ran.append(stale)
            return (True, "cleaned")

        monkeypatch.setattr(actions, "cleanup_stale", fake_cleanup)
        stale = {"containers": ["old-app"]}
        app._show_cleanup_offer(stale)
        app.update()
        # The fixed grid also has a cleanup button with the same label - take
        # the TRANSIENT one (its parent is the offer frame, not the fixed rows).
        fixed_parents = {str(app._primary_frame), str(app._secondary_frame)}
        run_buttons = [
            b
            for b in app._iter_buttons()
            if b["text"] == app._t("cleanup") and str(b.winfo_parent()) not in fixed_parents
        ]
        assert len(run_buttons) == 1
        run_buttons[0].invoke()
        app.update()
        assert ran == [stale]
        assert "cleaned" in app._status.get("1.0", "end")


class TestProgressBar:
    def test_determinate_progress_shows_value(self, app) -> None:
        app._update_progress(42, "building layer 3/7")
        app.update()
        assert app._progress_frame.winfo_ismapped()
        assert int(app._progress["value"]) == 42
        assert app._progress_label["text"] == "building layer 3/7"
        _screenshot(app, "progress_determinate")

    def test_indeterminate_mode_for_unknown_duration(self, app) -> None:
        app._update_progress(None, "waiting for health check")
        app.update()
        assert str(app._progress["mode"]) == "indeterminate"

    def test_hide_progress_unmaps_the_bar(self, app) -> None:
        app._update_progress(100, "done")
        app.update()
        app._hide_progress()
        app.update()
        assert not app._progress_frame.winfo_ismapped()


class TestBackgroundAndClose:
    def test_go_background_tray_mode_logs_and_keeps_controller(self, app, monkeypatch) -> None:
        monkeypatch.setattr(tray, "log_diagnostics", lambda c: None)
        monkeypatch.setattr(tray, "try_minimize_to_background", lambda root, c: "tray")
        app._go_background()
        assert app._tray is not None
        assert app._t("background_tray") in app._status.get("1.0", "end")

    def test_go_background_iconify_mode(self, app, monkeypatch) -> None:
        monkeypatch.setattr(tray, "log_diagnostics", lambda c: None)
        monkeypatch.setattr(tray, "try_minimize_to_background", lambda root, c: "iconify")
        app._go_background()
        assert app._tray is None
        assert app._t("background_iconified") in app._status.get("1.0", "end")

    def test_close_via_x_backgrounds_running_app(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "running"
        app._cfg.tray_enabled = True
        app._cfg.tray_minimize_on_close = True
        called: list[bool] = []
        monkeypatch.setattr(app, "_go_background", lambda *, via_close: called.append(via_close))
        app._on_close()
        assert called == [True]

    def test_close_via_x_quits_when_not_running(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "not_installed"
        called: list[bool] = []
        monkeypatch.setattr(app, "_quit", lambda: called.append(True))
        app._on_close()
        assert called == [True]

    def test_restore_window_stops_tray_and_reshows(self, app) -> None:
        class _FakeTray:
            stopped = False

            def stop(self):
                self.stopped = True

        fake = _FakeTray()
        app._tray = fake
        app.withdraw()
        app._restore_window()
        app.update()
        assert fake.stopped is True
        assert app._tray is None
        assert app.state() == "normal"
