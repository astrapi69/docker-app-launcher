"""Real-window tests for the CustomTkinter frontend.

Mirrors the core of ``test_gui_window`` against ``frontends.ctk`` - both
frontends render the same ``ui_model`` tables, so the SAME behaviour
assertions must hold. All actions are mocked; needs a display.
"""

from __future__ import annotations

import threading as _threading

import pytest

from docker_app_launcher import actions, frontends
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig
from docker_app_launcher.frontends import ctk as ctk_frontend
from tests.test_gui_window import _display_available, _screenshot


class _InlineThread:
    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


pytestmark = [
    pytest.mark.skipif(not ctk_frontend.HAS_CTK, reason="customtkinter not installed (the 'ctk' extra)"),
    pytest.mark.skipif(not _display_available(), reason="no display (run under xvfb-run)"),
]


@pytest.fixture
def gui_state(monkeypatch):
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
    # Dark mode for every test window so the screenshot set is consistent
    # across all three frontends.
    import customtkinter

    customtkinter.set_appearance_mode("dark")
    config = LauncherConfig(
        app_name="Ctk App",
        default_port=8080,
        locale="en",
        cleanup_on_start=False,
        update_check_enabled=False,
    )
    window = ctk_frontend.CtkLauncherApp(config)
    window.update()
    yield window
    window.destroy()


class TestRegistry:
    def test_ctk_is_a_builtin_frontend(self) -> None:
        assert "ctk" in frontends.available_frontends()
        assert frontends.get_frontend("ctk") is ctk_frontend

    def test_run_contract(self) -> None:
        assert callable(frontends.get_frontend("ctk").run)


class TestWindow:
    def test_builds_with_title_and_all_buttons(self, app) -> None:
        assert app.title() == "Ctk App"
        from docker_app_launcher.ui_model import PRIMARY_BUTTONS, SECONDARY_BUTTONS

        assert set(app._buttons) == set(PRIMARY_BUTTONS) | set(SECONDARY_BUTTONS)
        _screenshot(app, "ctk_not_installed_en")

    def test_button_states_match_ui_model(self, app, gui_state) -> None:
        from docker_app_launcher.ui_model import button_enabled

        for state in ("not_installed", "stopped", "running", "no_docker"):
            gui_state["value"] = state
            app._refresh()
            app.update()
            for name, btn in app._buttons.items():
                expected = "normal" if button_enabled(state, name) else "disabled"
                assert str(btn.cget("state")) == expected, f"{name} in {state}"

    def test_no_docker_shows_help_panel(self, app, gui_state) -> None:
        gui_state["value"] = "no_docker"
        app._refresh()
        app.update()
        assert app._docker_help_frame.winfo_ismapped()
        _screenshot(app, "ctk_no_docker_en")

    def test_language_switch_relabels(self, app, gui_state) -> None:
        app._locale_var.set(LOCALE_LABELS["de"])
        app._on_locale_change()
        app.update()
        assert app._cfg.locale == "de"
        assert any("nstallieren" in str(btn.cget("text")) for btn in app._buttons.values())
        _screenshot(app, "ctk_not_installed_de")

    def test_log_and_copy(self, app) -> None:
        app._log("ctk log line")
        assert "ctk log line" in app._status.get("1.0", "end")
        app._copy_log()
        app.update()
        assert "ctk log line" in app.clipboard_get()

    def test_port_validation_indicator(self, app) -> None:
        app._port_var.set("nope")
        app._validate_port()
        assert app._port_indicator.cget("text") == "✗"
        app._port_var.set("8080")
        app._validate_port()
        assert app._port_indicator.cget("text") == "✓"


class TestActionFlow:
    def test_action_success_logs_and_reenables(self, app, gui_state, monkeypatch) -> None:
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(ctk_frontend, "dispatch_action", lambda action_id, cfg, **k: (True, "ctk done"))
        app._buttons["install"].invoke()
        app.update()
        assert "ctk done" in app._status.get("1.0", "end")
        assert str(app._buttons["install"].cget("state")) == "normal"

    def test_busy_disables_every_button(self, app) -> None:
        app._set_busy(True)
        assert all(str(btn.cget("state")) == "disabled" for btn in app._iter_buttons())
        app._set_busy(False)
        assert all(str(btn.cget("state")) == "normal" for btn in app._iter_buttons())

    def test_cleanup_none_found(self, app, monkeypatch) -> None:
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda c: {})
        app._run_manual_cleanup()
        app.update()
        assert app._t("cleanup_none") in app._status.get("1.0", "end")


class TestClose:
    def test_close_quits_when_not_running(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "not_installed"
        called: list[bool] = []
        monkeypatch.setattr(app, "_quit", lambda: called.append(True))
        app._on_close()
        assert called == [True]

    def test_close_backgrounds_running_app(self, app, gui_state, monkeypatch) -> None:
        gui_state["value"] = "running"
        app._cfg.tray_enabled = True
        app._cfg.tray_minimize_on_close = True
        called: list[bool] = []
        monkeypatch.setattr(app, "_go_background", lambda *, via_close: called.append(via_close))
        app._on_close()
        assert called == [True]


class TestRunGuard:
    def test_run_raises_without_the_extra(self, monkeypatch) -> None:
        monkeypatch.setattr(ctk_frontend, "HAS_CTK", False)
        with pytest.raises(RuntimeError, match="ctk"):
            ctk_frontend.run(LauncherConfig(app_name="X"))


@pytest.mark.parametrize("state", ["not_installed", "stopped", "running"])
def test_screenshot_states(app, gui_state, state) -> None:
    gui_state["value"] = state
    app._refresh()
    app.update()
    _screenshot(app, f"ctk_{state}_en")
