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

import contextlib
import os
import threading as _threading
import tkinter as tk
from pathlib import Path

import pytest

from docker_app_launcher import actions, gui, tray
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.frontends import tk_window


def _display_available() -> bool:
    try:
        probe = tk.Tk()
    except tk.TclError:
        return False
    probe.destroy()
    return True


pytestmark = pytest.mark.skipif(not _display_available(), reason="no display (run under xvfb-run)")

SCREENSHOT_DIR = Path(os.environ.get("DAL_SCREENSHOT_DIR", "test-screenshots"))

# Dark palette applied to every test window so the screenshot set is easy on
# the eyes. Classic Tk has no theme system, so the colors are pushed onto each
# widget recursively; ttk widgets (Combobox, Progressbar, Separator) have no
# bg/fg options and are skipped via the TclError guard.
_DARK = {
    "bg": "#1e1e1e",
    "fg": "#e0e0e0",
    "entry_bg": "#2d2d2d",
    "button_bg": "#333333",
    "active_bg": "#444444",
}


def apply_dark_theme(root: tk.Misc) -> None:
    """Best-effort dark styling for a plain-Tk window (tests/screenshots only).

    Safe to call on ANY frontend window and at any time: only pure-tkinter
    widgets are touched (CustomTkinter subclasses tk widgets but styles
    itself), and dynamically created widgets get styled on the next call.
    """
    try:
        from tkinter import ttk as _ttk

        style = _ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=_DARK["bg"], foreground=_DARK["fg"], fieldbackground=_DARK["entry_bg"])
        style.map("TCombobox", fieldbackground=[("readonly", _DARK["entry_bg"])])
    except tk.TclError:
        pass
    with contextlib.suppress(tk.TclError):
        root.configure(bg=_DARK["bg"])  # type: ignore[call-arg]
    stack: list[tk.Misc] = list(root.winfo_children())
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if not type(widget).__module__.startswith("tkinter"):
            continue  # CustomTkinter (and friends) style themselves
        try:
            if isinstance(widget, tk.Button):
                widget.configure(
                    bg=_DARK["button_bg"],
                    fg=_DARK["fg"],
                    activebackground=_DARK["active_bg"],
                    activeforeground=_DARK["fg"],
                    disabledforeground="#777777",
                )
            elif isinstance(widget, tk.Entry):
                widget.configure(
                    bg=_DARK["entry_bg"], fg=_DARK["fg"], insertbackground=_DARK["fg"], disabledbackground=_DARK["bg"]
                )
            elif isinstance(widget, tk.Text):
                widget.configure(bg=_DARK["entry_bg"], fg=_DARK["fg"], insertbackground=_DARK["fg"])
            elif isinstance(widget, (tk.Frame, tk.Label)):
                widget.configure(bg=_DARK["bg"])
                if isinstance(widget, tk.Label):
                    widget.configure(fg=_DARK["fg"])
        except tk.TclError:
            # ttk widgets and platform quirks: no bg/fg options - skip.
            pass


def _keep_off_screen(window: tk.Tk) -> None:
    """Test windows must never flash on a developer's desktop.

    Bypass the window manager (override-redirect - the WM would clamp an
    off-screen position back into view) and map far outside the visible
    screen. Skipped for DAL_SCREENSHOTS runs: the capture backends can only
    grab what lies inside the X root, and those runs are explicitly asked
    for. Set DAL_SHOW_TEST_WINDOWS=1 to watch the windows for debugging.
    """
    if os.environ.get("DAL_SCREENSHOTS") or os.environ.get("DAL_SHOW_TEST_WINDOWS"):
        return
    with contextlib.suppress(tk.TclError):
        window.wm_overrideredirect(True)
        window.geometry("+6000+6000")


def _screenshot(app: gui.LauncherApp, name: str) -> None:
    """Best-effort window screenshot; never fails the test.

    Backend chain: pyautogui -> ImageMagick ``import`` -> Pillow ImageGrab.
    pyautogui alone proved unreliable both under xvfb (CI artifacts silently
    contained no Tk shots) and on Wayland desktops, so a miss falls through
    to the next backend, and a total miss surfaces as a pytest warning
    instead of a swallowed print.
    """
    if not os.environ.get("DAL_SCREENSHOTS"):
        return
    import subprocess
    import warnings

    # Restyle right before the shot: windows built outside the app fixture
    # (per-language tests) and widgets created after fixture setup (docker
    # help, cleanup offer) must be dark too. No-op on CTk windows.
    apply_dark_theme(app)
    app.update_idletasks()
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    errors: list[str] = []
    try:
        import pyautogui

        pyautogui.screenshot(str(path), region=(x, y, w, h))
        return
    except Exception as exc:  # noqa: BLE001 - fall through to the next backend
        errors.append(f"pyautogui: {exc}")
    try:
        subprocess.run(
            ["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", "+repage", str(path)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return
    except Exception as exc:  # noqa: BLE001 - fall through to the next backend
        errors.append(f"imagemagick: {exc}")
    try:
        from PIL import ImageGrab

        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
        return
    except Exception as exc:  # noqa: BLE001 - screenshots are documentation, not assertions
        errors.append(f"ImageGrab: {exc}")
    warnings.warn(f"screenshot {name} failed on every backend: {'; '.join(errors)}", stacklevel=2)


@pytest.fixture
def gui_state(monkeypatch):
    """Mock every action the window calls; the dict controls the app state."""
    state = {"value": "not_installed"}
    monkeypatch.setattr(actions, "get_state", lambda c: state["value"])
    monkeypatch.setattr(
        actions,
        "check_docker_detailed",
        lambda c, **k: {
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
    monkeypatch.setattr(actions, "find_stale_artifacts", lambda c, **k: {})
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
    _keep_off_screen(window)
    apply_dark_theme(window)
    window.update()
    yield window
    window.destroy()


class TestWindowConstruction:
    def test_window_builds_with_title_and_version(self, app) -> None:
        import docker_app_launcher

        assert app.title().startswith("Test App")
        assert docker_app_launcher.__version__ in app.title()  # never hardcoded (#30)
        _screenshot(app, "not_installed_en")

    def test_version_is_the_first_log_line(self, app) -> None:
        import docker_app_launcher

        first_line = app._status.get("1.0", "end").splitlines()[0]
        assert docker_app_launcher.__version__ in first_line

    def test_log_panel_lines_reach_the_logging_system(self, app, caplog) -> None:
        with caplog.at_level("INFO", logger="docker_app_launcher.ui.panel"):
            app._log("mirrored to launcher.log")
            app._log("panel error", tag="err")
        messages = {r.message: r.levelname for r in caplog.records}
        assert messages.get("mirrored to launcher.log") == "INFO"
        assert messages.get("panel error") == "ERROR"

    def test_tk_callback_exception_logged_and_shown(self, app, caplog) -> None:
        try:
            raise RuntimeError("callback blew up")
        except RuntimeError as exc:
            with caplog.at_level("ERROR"):
                app.report_callback_exception(type(exc), exc, exc.__traceback__)
        assert any(r.exc_info and "callback blew up" in str(r.exc_info[1]) for r in caplog.records)
        assert "callback blew up" in app._status.get("1.0", "end")

    def test_tk_callback_exception_survives_broken_panel(self, app, caplog) -> None:
        # Logging must still happen when the Text widget is already destroyed.
        app._status.destroy()
        try:
            raise RuntimeError("late crash")
        except RuntimeError as exc:
            with caplog.at_level("ERROR"):
                app.report_callback_exception(type(exc), exc, exc.__traceback__)
        assert any(r.exc_info and "late crash" in str(r.exc_info[1]) for r in caplog.records)

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
        app._clear_status()  # discard the startup version line
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
    _keep_off_screen(window)
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
        monkeypatch.setattr(tk_window, "dispatch_action", lambda action_id, cfg, **k: (True, "install done"))
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
        monkeypatch.setattr(tk_window, "dispatch_action", lambda action_id, cfg, **k: (False, "install blew up"))
        app._buttons["install"].invoke()
        app.update()
        assert "install blew up" in app._status.get("1.0", "end")

    def test_install_persists_typed_port(self, app, gui_state, inline_threads, monkeypatch) -> None:
        persisted: list[int] = []

        def record_port(c, p):
            persisted.append(p)
            return p

        monkeypatch.setattr(actions, "set_port", record_port)
        monkeypatch.setattr(tk_window, "dispatch_action", lambda action_id, cfg, **k: (True, "ok"))
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
        monkeypatch.setattr(tk_window, "dispatch_action", lambda action_id, cfg, **k: (True, "ok"))
        app._port_var.set("9999")
        app._on_action("stop")
        app.update()
        assert persisted == []

    def test_worker_crash_releases_busy_and_logs(self, app, gui_state, inline_threads, monkeypatch, caplog) -> None:
        """P1: an exception behind the actions promise must not freeze the
        window in its busy state or vanish without a trace."""

        def explode(action_id, cfg, **k):
            raise RuntimeError("worker blew up")

        monkeypatch.setattr(tk_window, "dispatch_action", explode)
        with caplog.at_level("ERROR", logger="docker_app_launcher.ui_model"):
            app._buttons["install"].invoke()
            app.update()
        assert app._buttons["install"]["state"] == "normal"  # busy released
        assert "worker blew up" in app._status.get("1.0", "end")
        assert any(r.exc_info for r in caplog.records)

    def _force_permission_state(self, app, gui_state, monkeypatch) -> None:
        """Drive the window into the WORDIEST known state (#47): the
        docker_no_permission detail plus the usermod command line."""
        from docker_app_launcher import i18n

        detail = i18n.t("docker_no_permission", app._cfg)
        monkeypatch.setattr(
            actions,
            "check_docker_detailed",
            lambda c, **k: {
                "status": "permission",
                "detail": detail,
                "command": "sudo usermod -aG docker $USER",
                "can_start": False,
                "installed": True,
                "can_fix_permission": True,
                "platform": "Linux",
            },
        )
        gui_state["value"] = "no_docker"
        app._refresh()
        app.update()

    def test_permission_message_fits_the_window_width(self, app, gui_state, monkeypatch) -> None:
        # RED before #47: no wraplength -> the label demands more width than
        # the window has and the text clips at the edge.
        self._force_permission_state(app, gui_state, monkeypatch)
        assert app._state_label.winfo_reqwidth() <= app.winfo_width(), (
            f"state label needs {app._state_label.winfo_reqwidth()}px, window is {app.winfo_width()}px - text clipped"
        )
        _screenshot(app, "no_docker_permission_wrapped")

    def test_state_label_rewraps_on_resize(self, app, gui_state, monkeypatch) -> None:
        # Growing the window must widen the wrap, not leave a narrow column.
        self._force_permission_state(app, gui_state, monkeypatch)
        app.geometry("900x520")
        app.update()
        assert int(app._state_label.cget("wraplength")) > 700

    def test_every_state_fits_the_window_width(self, app, gui_state) -> None:
        for state in ("not_installed", "stopped", "running", "no_docker"):
            gui_state["value"] = state
            app._refresh()
            app.update()
            assert app._state_label.winfo_reqwidth() <= app.winfo_width(), f"clipped in state {state!r}"

    def test_focus_poll_consumes_marker_and_raises_window(self, app, monkeypatch) -> None:
        # #31: a pending focus request brings the window up exactly once.
        from docker_app_launcher import lockfile as _lockfile

        raised: list[bool] = []
        monkeypatch.setattr(app, "_bring_to_front", lambda: raised.append(True))
        _lockfile.request_focus(app._cfg.lock_path)
        app._poll_focus_request()
        assert raised == [True]
        assert not _lockfile.focus_request_path(app._cfg.lock_path).is_file()
        app._poll_focus_request()  # no pending request -> no second raise
        assert raised == [True]

    def test_initial_focus_follows_the_state(self, app, gui_state) -> None:
        # #31: entering a state puts keyboard focus on its primary action.
        recorded: list[str] = []
        for name, btn in app._buttons.items():
            btn.focus_set = lambda n=name: recorded.append(n)
        app._focused_state = None
        for state, expected in (("not_installed", "install"), ("stopped", "start"), ("running", "open_browser")):
            gui_state["value"] = state
            recorded.clear()
            app._refresh()
            assert recorded == [expected], f"state {state!r}: focus went to {recorded}"

    def test_refresh_without_state_change_keeps_focus(self, app, gui_state) -> None:
        # Polling refreshes must never steal focus from the port field.
        recorded: list[str] = []
        for name, btn in app._buttons.items():
            btn.focus_set = lambda n=name: recorded.append(n)
        gui_state["value"] = "running"
        app._focused_state = None
        app._refresh()
        recorded.clear()
        app._refresh()  # same state again
        assert recorded == []

    def test_window_is_resizable_by_default(self, app) -> None:
        assert tuple(map(int, app.resizable())) == (1, 1)

    def test_window_resizable_opt_out(self, gui_state) -> None:
        config = LauncherConfig(
            app_name="Fixed App",
            default_port=8080,
            locale="en",
            cleanup_on_start=False,
            update_check_enabled=False,
            window_resizable=False,
        )
        window = gui.LauncherApp(config)
        try:
            assert tuple(map(int, window.resizable())) == (0, 0)
        finally:
            window.destroy()

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
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda c, **k: {})
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
        # Since #33 the offer button has its own unambiguous label.
        run_buttons = [b for b in app._iter_buttons() if b["text"] == app._t("cleanup_now")]
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


class TestFixDockerPermission:
    """Self-repair button for the docker-group case (#27)."""

    def _permission_state(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "no_docker"
        monkeypatch.setattr(
            actions,
            "check_docker_detailed",
            lambda c, **k: {
                "status": "no_permission",
                "platform": "Linux",
                "detail": "no permission",
                "command": "sudo usermod -aG docker $USER",
                "can_start": False,
                "installed": True,
                "can_fix_permission": True,
            },
        )
        app._refresh()
        app.update()

    def _fix_buttons(self, app) -> list[tk.Button]:
        label = app._t("fix_docker_permission")
        return [b for b in app._iter_buttons() if b["text"] == label]

    def test_button_shown_when_fixable(self, app, gui_state, monkeypatch) -> None:
        self._permission_state(app, gui_state, monkeypatch)
        assert len(self._fix_buttons(app)) == 1
        _screenshot(app, "no_docker_permission_en")

    def test_button_absent_without_fix_capability(self, app, gui_state) -> None:
        gui_state["value"] = "no_docker"
        app._refresh()  # fixture's default mock has no can_fix_permission
        app.update()
        assert self._fix_buttons(app) == []

    def test_decline_logs_cancel_and_changes_nothing(self, app, gui_state, monkeypatch) -> None:
        self._permission_state(app, gui_state, monkeypatch)
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: False)
        called: list[str] = []

        def record_call(c):
            called.append("x")
            return (True, "no")

        monkeypatch.setattr(actions, "add_user_to_docker_group", record_call)
        self._fix_buttons(app)[0].invoke()
        app.update()
        assert called == []
        assert app._t("docker_group_cancelled") in app._status.get("1.0", "end")

    def test_accept_runs_repair_and_keeps_relogin_message(self, app, gui_state, monkeypatch) -> None:
        self._permission_state(app, gui_state, monkeypatch)
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
        monkeypatch.setattr(_threading, "Thread", _InlineThread)

        from docker_app_launcher import i18n

        def fake_repair(cfg):
            return (True, i18n.t("docker_group_added", cfg))

        monkeypatch.setattr(actions, "add_user_to_docker_group", fake_repair)
        self._fix_buttons(app)[0].invoke()
        app.update()
        log = app._status.get("1.0", "end").lower()
        assert "log out" in log  # success message still demands the re-login
        assert "ready" not in log

    def test_repair_failure_is_reported(self, app, gui_state, monkeypatch) -> None:
        self._permission_state(app, gui_state, monkeypatch)
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(actions, "add_user_to_docker_group", lambda c: (False, "pkexec dismissed"))
        self._fix_buttons(app)[0].invoke()
        app.update()
        assert "pkexec dismissed" in app._status.get("1.0", "end")


class TestStartDockerWaits:
    """After a successful Docker-Desktop/daemon start the GUI must WAIT for
    the daemon (VM boot) instead of instantly reporting 'not started' (#28)."""

    def test_successful_start_polls_until_ready(self, app, gui_state, monkeypatch) -> None:
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(actions, "start_docker_desktop", lambda c: (True, "Docker Desktop starting..."))
        waited: list[str] = []

        def fake_wait(cfg, *, on_progress=None):
            if on_progress is not None:
                on_progress(None, "waiting label")
            waited.append("polled")
            return (True, "Docker is running.")

        monkeypatch.setattr(actions, "wait_for_docker", fake_wait)
        app._start_docker({"platform": "Darwin"})
        app.update()
        assert waited == ["polled"]
        assert "Docker is running." in app._status.get("1.0", "end")

    def test_failed_start_does_not_poll(self, app, gui_state, monkeypatch) -> None:
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(actions, "start_docker_desktop", lambda c: (False, "Docker Desktop not found."))

        def must_not_poll(cfg, **k):
            raise AssertionError("wait_for_docker must not run after a failed start")

        monkeypatch.setattr(actions, "wait_for_docker", must_not_poll)
        app._start_docker({"platform": "Darwin"})
        app.update()
        assert "not found" in app._status.get("1.0", "end")


class TestAboutDialog:
    """About button: version + platform + click-through to the issue tracker (#30)."""

    def test_about_button_exists_and_enabled_even_without_docker(self, app, gui_state) -> None:
        gui_state["value"] = "no_docker"
        app._refresh()
        app.update()
        assert str(app._buttons["info"]["state"]) == "normal"

    def test_accept_opens_issue_tracker(self, app, monkeypatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)

        def record_open(url):
            opened.append(url)
            return True

        monkeypatch.setattr(actions, "open_url", record_open)
        app._buttons["info"].invoke()
        assert opened and opened[0].endswith("/issues")

    def test_decline_opens_nothing(self, app, monkeypatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: False)

        def record_open(url):
            opened.append(url)
            return True

        monkeypatch.setattr(actions, "open_url", record_open)
        app._buttons["info"].invoke()
        assert opened == []


class TestDetectionStepsVisible:
    """Multi-step docker detection streams into the visible log (#30)."""

    def test_sweep_steps_appear_in_the_log(self, app, gui_state, monkeypatch) -> None:
        def fake_detailed(cfg, *, on_step=None):
            if on_step is not None:
                on_step("Checking Docker context 'desktop-linux' (unix:///desk.sock)…")
                on_step("Checking Docker context 'rootless' (unix:///root.sock)…")
            return {
                "status": "not_running",
                "platform": "Linux",
                "detail": "not running",
                "command": "",
                "can_start": True,
                "installed": True,
                "can_fix_permission": False,
            }

        gui_state["value"] = "no_docker"
        monkeypatch.setattr(actions, "check_docker_detailed", fake_detailed)
        app._refresh()
        app.update()
        log = app._status.get("1.0", "end")
        assert "desktop-linux" in log and "rootless" in log  # each sub-step, not just the end state


class TestUninstallConfirmation:
    """Uninstall is destructive - a single misclick must not trigger it (#31 audit)."""

    def test_decline_does_not_dispatch(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "running"
        app._refresh()
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: False)
        dispatched: list[str] = []
        monkeypatch.setattr(app, "_on_action", lambda action_id: dispatched.append(action_id))
        app._buttons["uninstall"].invoke()
        assert dispatched == []

    def test_accept_dispatches_uninstall(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "running"
        app._refresh()
        monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **k: True)
        dispatched: list[str] = []
        monkeypatch.setattr(app, "_on_action", lambda action_id: dispatched.append(action_id))
        app._buttons["uninstall"].invoke()
        assert dispatched == ["uninstall"]


class TestWindowGeometryMemory:
    """The window reopens where the user left it (#31 audit)."""

    def test_stored_geometry_is_applied_on_start(self, gui_state, monkeypatch) -> None:
        monkeypatch.setattr(actions, "resolve_window_geometry", lambda c: "640x540+11+22")
        config = LauncherConfig(
            app_name="Geo App", default_port=8080, locale="en", cleanup_on_start=False, update_check_enabled=False
        )
        window = gui.LauncherApp(config)
        _keep_off_screen(window)
        try:
            window.update()
            geometry = window.winfo_geometry()
            assert geometry.startswith("640x540")
        finally:
            window.destroy()

    def test_quit_persists_geometry(self, gui_state, monkeypatch) -> None:
        saved: list[str] = []
        monkeypatch.setattr(actions, "set_window_geometry", lambda c, g: saved.append(g))
        config = LauncherConfig(
            app_name="Geo App", default_port=8080, locale="en", cleanup_on_start=False, update_check_enabled=False
        )
        window = gui.LauncherApp(config)
        _keep_off_screen(window)
        window.update()
        window._quit()  # destroys the window itself - no fixture double-destroy
        assert len(saved) == 1
        assert "x" in saved[0] and "+" in saved[0]


class TestNoDuplicateButtonLabels:
    """Two different actions must never share one label (#33): the fixed
    cleanup (scan) button and the transient offer button collided as
    'cleanup'."""

    def test_offer_creates_no_identically_labelled_buttons(self, app, gui_state) -> None:
        app._show_cleanup_offer({"containers": ["old-app"]})
        app.update()
        labels = [str(b["text"]) for b in app._iter_buttons()]
        duplicates = {label for label in labels if labels.count(label) > 1}
        assert duplicates == set(), f"identically labelled buttons: {duplicates}"
        _screenshot(app, "cleanup_offer_labels_en")

    def test_offer_button_says_clean_up_now(self, app, gui_state) -> None:
        app._show_cleanup_offer({"containers": ["old-app"]})
        app.update()
        labels = [str(b["text"]) for b in app._iter_buttons()]
        assert app._t("cleanup_now") in labels  # the offer acts on FOUND artifacts
        assert labels.count(app._t("cleanup")) == 1  # the fixed scan button keeps its label
