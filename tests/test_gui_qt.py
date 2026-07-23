"""Real-window tests for the PySide6 (Qt) frontend.

Runs on the ``offscreen`` Qt platform - no display, no xvfb needed - so this
suite is the one GUI suite that always runs, even on a bare CI box.
Screenshots use Qt's native ``widget.grab()`` (works offscreen too).
Behaviour assertions mirror the Tk/CTk suites: same ``ui_model``, same
expectations.
"""

from __future__ import annotations

import os
import threading as _threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from docker_app_launcher import actions, frontends
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig
from docker_app_launcher.frontends import qt as qt_frontend
from tests.test_gui_window import SCREENSHOT_DIR

pytestmark = pytest.mark.skipif(not qt_frontend.HAS_QT, reason="PySide6 not installed (the 'qt' extra)")


def _qt_screenshot(window, name: str) -> None:
    if not os.environ.get("DAL_SCREENSHOTS"):
        return
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(SCREENSHOT_DIR / f"{name}.png"))


class _InlineThread:
    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # Dark mode for every test window: Fusion + a dark palette is the
    # portable Qt recipe (offscreen has no OS theme to follow).
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#1e1e1e"),
        (QPalette.ColorRole.WindowText, "#e0e0e0"),
        (QPalette.ColorRole.Base, "#2d2d2d"),
        (QPalette.ColorRole.AlternateBase, "#252525"),
        (QPalette.ColorRole.Text, "#e0e0e0"),
        (QPalette.ColorRole.Button, "#333333"),
        (QPalette.ColorRole.ButtonText, "#e0e0e0"),
        (QPalette.ColorRole.ToolTipBase, "#333333"),
        (QPalette.ColorRole.ToolTipText, "#e0e0e0"),
        (QPalette.ColorRole.Highlight, "#2a5db0"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#777777"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#777777"))
    app.setPalette(palette)
    yield app


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
            "platform": "Linux",
        },
    )
    monkeypatch.setattr(actions, "check_port", lambda p: (True, ""))
    monkeypatch.setattr(actions, "resolve_port", lambda c: c.default_port)
    monkeypatch.setattr(actions, "resolve_locale", lambda c: c.locale if c.locale != "auto" else "en")
    monkeypatch.setattr(actions, "set_locale", lambda c, code: code)
    monkeypatch.setattr(actions, "find_stale_artifacts", lambda c: {})
    return state


@pytest.fixture
def app(qapp, gui_state):
    config = LauncherConfig(
        app_name="Qt App",
        default_port=8080,
        locale="en",
        cleanup_on_start=False,
        update_check_enabled=False,
    )
    window = qt_frontend.QtLauncherApp(config)
    qapp.processEvents()
    yield window
    window._stop_tray()
    window.deleteLater()
    qapp.processEvents()


class TestRegistry:
    def test_qt_is_a_builtin_frontend(self) -> None:
        assert "qt" in frontends.available_frontends()
        assert frontends.get_frontend("qt") is qt_frontend

    def test_run_contract(self) -> None:
        assert callable(frontends.get_frontend("qt").run)


class TestWindow:
    def test_builds_with_title_and_all_buttons(self, app) -> None:
        assert app.windowTitle() == "Qt App"
        from docker_app_launcher.ui_model import PRIMARY_BUTTONS, SECONDARY_BUTTONS

        assert set(app._buttons) == set(PRIMARY_BUTTONS) | set(SECONDARY_BUTTONS)
        _qt_screenshot(app, "qt_not_installed_en")

    def test_button_states_match_ui_model(self, app, qapp, gui_state) -> None:
        from docker_app_launcher.ui_model import button_enabled

        for state in ("not_installed", "stopped", "running", "no_docker"):
            gui_state["value"] = state
            app._refresh()
            qapp.processEvents()
            for name, btn in app._buttons.items():
                assert btn.isEnabled() == button_enabled(state, name), f"{name} in {state}"

    def test_no_docker_shows_help_panel_with_detail(self, app, qapp, gui_state) -> None:
        gui_state["value"] = "no_docker"
        app._refresh()
        qapp.processEvents()
        assert not app._docker_help.isHidden()
        assert "daemon not running" in app._state_label.text()
        _qt_screenshot(app, "qt_no_docker_en")

    def test_tooltip_reason_on_disabled_button(self, app, gui_state) -> None:
        gui_state["value"] = "not_installed"
        app._refresh()
        assert app._buttons["stop"].toolTip() != ""
        assert app._buttons["install"].toolTip() == ""

    def test_language_switch_relabels(self, app, qapp, gui_state) -> None:
        app._locale_combo.setCurrentText(LOCALE_LABELS["de"])
        qapp.processEvents()
        assert app._cfg.locale == "de"
        assert any("nstallieren" in btn.text() for btn in app._buttons.values())
        _qt_screenshot(app, "qt_not_installed_de")

    def test_log_and_copy(self, app, qapp) -> None:
        from PySide6.QtGui import QGuiApplication

        app._log("qt log line")
        assert "qt log line" in app._status.toPlainText()
        app._copy_log()
        qapp.processEvents()
        assert "qt log line" in QGuiApplication.clipboard().text()

    def test_port_validation_indicator(self, app) -> None:
        app._port_entry.setText("nope")
        app._validate_port()
        assert app._port_indicator.text() == "✗"
        app._port_entry.setText("8080")
        app._validate_port()
        assert app._port_indicator.text() == "✓"


class TestActionFlow:
    def test_action_success_logs_and_reenables(self, app, qapp, gui_state, monkeypatch) -> None:
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(qt_frontend, "dispatch_action", lambda action_id, cfg, **k: (True, "qt done"))
        app._buttons["install"].click()
        qapp.processEvents()
        assert "qt done" in app._status.toPlainText()
        assert app._buttons["install"].isEnabled()

    def test_busy_disables_every_button(self, app) -> None:
        app._set_busy(True)
        assert all(not btn.isEnabled() for btn in app._iter_buttons())
        app._set_busy(False)
        assert all(btn.isEnabled() for btn in app._iter_buttons())

    def test_cleanup_none_found(self, app, qapp, monkeypatch) -> None:
        monkeypatch.setattr(_threading, "Thread", _InlineThread)
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda c: {})
        app._run_manual_cleanup()
        qapp.processEvents()
        assert app._t("cleanup_none") in app._status.toPlainText()

    def test_progress_determinate_and_indeterminate(self, app, qapp) -> None:
        app._update_progress(42, "layer 3/7")
        qapp.processEvents()
        assert not app._progress_box.isHidden()
        assert app._progress.value() == 42
        app._update_progress(None, "health check")
        assert app._progress.maximum() == 0  # Qt's indeterminate mode
        app._hide_progress()
        assert app._progress_box.isHidden()


class TestClose:
    def test_close_quits_when_not_running(self, app, qapp, gui_state) -> None:
        gui_state["value"] = "not_installed"
        assert app.close() is True  # closeEvent accepted

    def test_close_backgrounds_running_app(self, app, qapp, gui_state, monkeypatch) -> None:
        gui_state["value"] = "running"
        app._cfg.tray_enabled = True
        app._cfg.tray_minimize_on_close = True
        called: list[bool] = []
        monkeypatch.setattr(app, "_go_background", lambda *, via_close: called.append(via_close))
        assert app.close() is False  # closeEvent ignored, window stays alive
        assert called == [True]

    def test_pystray_adapter_methods(self, app) -> None:
        # try_minimize_to_background duck-types withdraw/iconify; the Qt
        # window must satisfy that contract.
        app.withdraw()
        assert app.isHidden()
        app.iconify()
        app.showNormal()


class TestRunGuard:
    def test_run_raises_without_the_extra(self, monkeypatch) -> None:
        monkeypatch.setattr(qt_frontend, "HAS_QT", False)
        with pytest.raises(RuntimeError, match="qt"):
            qt_frontend.run(LauncherConfig(app_name="X"))


@pytest.mark.parametrize("state", ["not_installed", "stopped", "running"])
def test_screenshot_states(app, qapp, gui_state, state) -> None:
    gui_state["value"] = state
    app._refresh()
    qapp.processEvents()
    _qt_screenshot(app, f"qt_{state}_en")
