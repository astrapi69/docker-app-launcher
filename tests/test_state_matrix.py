"""Systematic rendering matrix: every frontend x every known app state.

Every screenshot-discovered bug of the past (duplicate button labels,
missing version in the title, raw placeholder branding) was a plain UI
state/rendering defect that a systematic inspection of the rendered widget
tree would have caught without a human sending screenshots. This module IS
that inspection, and it is deliberately driven by the CENTRAL state table
``ui_model.BUTTON_STATES``: a future state added there is checked
automatically — a state that exists in code but not in the table cannot
even render (``button_enabled`` would disable everything), and
``test_state_tables_are_complete`` pins the tables against each other. No
per-state test can be forgotten because there are no per-state tests.

Checked per frontend and state:
- window title: config app name + the real installed version, no placeholder
- the full button set: every expected button present, every label unique
- per-state enablement exactly equal to the ``BUTTON_STATES`` table
- port-field editability equal to ``port_editable``
- multi-step docker detection streaming into the visible log

HONEST LIMIT: this inspects windows built from the SOURCE tree. A bug that
only exists in the frozen PyInstaller binary (like the unbundled i18n
catalogs, #34) needs the frozen-binary CI step (#38) — tracked separately.
"""

from __future__ import annotations

import os

import pytest

from docker_app_launcher import actions, i18n, ui_model
from docker_app_launcher.config import LauncherConfig

KNOWN_STATES = sorted(ui_model.BUTTON_STATES)
ALL_BUTTONS = ui_model.PRIMARY_BUTTONS + ui_model.SECONDARY_BUTTONS


def test_state_tables_are_complete() -> None:
    """Every heading state has a button table and vice versa - the guard that
    makes it impossible to introduce a state outside the matrix."""
    assert set(ui_model._STATE_KEYS) == set(ui_model.BUTTON_STATES)
    for state, table in ui_model.BUTTON_STATES.items():
        assert set(table) == set(ALL_BUTTONS), f"button table for {state!r} out of sync"


def _config() -> LauncherConfig:
    return LauncherConfig(
        app_name="Matrix App",
        default_port=8080,
        locale="en",
        cleanup_on_start=False,
        update_check_enabled=False,
    )


@pytest.fixture
def matrix_state(monkeypatch):
    state = {"value": "not_installed"}
    monkeypatch.setattr(actions, "get_state", lambda c: state["value"])
    monkeypatch.setattr(
        actions,
        "check_docker_detailed",
        lambda c, **k: {
            "status": "not_running",
            "platform": "Linux",
            "detail": "daemon not running",
            "command": "",
            "can_start": True,
            "installed": True,
            "can_fix_permission": False,
        },
    )
    monkeypatch.setattr(actions, "check_port", lambda p: (True, ""))
    monkeypatch.setattr(actions, "resolve_port", lambda c: c.default_port)
    monkeypatch.setattr(actions, "resolve_locale", lambda c: "en")
    monkeypatch.setattr(actions, "set_locale", lambda c, code: code)
    monkeypatch.setattr(actions, "find_stale_artifacts", lambda c, **k: {})
    return state


class _WindowAdapter:
    """Uniform view onto a frontend window for matrix assertions."""

    def __init__(self, frontend: str, window, refresh, close) -> None:
        self.frontend = frontend
        self.window = window
        self.refresh = refresh
        self.close = close

    def title(self) -> str:
        if self.frontend == "qt":
            return str(self.window.windowTitle())
        return str(self.window.title())

    def button_labels(self) -> dict[str, str]:
        if self.frontend == "qt":
            return {name: str(btn.text()) for name, btn in self.window._buttons.items()}
        return {name: str(btn.cget("text")) for name, btn in self.window._buttons.items()}

    def button_enabled(self, name: str) -> bool:
        btn = self.window._buttons[name]
        if self.frontend == "qt":
            return bool(btn.isEnabled())
        return str(btn.cget("state")) == "normal"

    def port_editable(self) -> bool:
        if self.frontend == "qt":
            return bool(self.window._port_entry.isEnabled())
        return str(self.window._port_entry.cget("state")) == "normal"

    def log_text(self) -> str:
        if self.frontend == "qt":
            return str(self.window._status.toPlainText())
        return str(self.window._status.get("1.0", "end"))


def _build_window(frontend: str, matrix_state) -> _WindowAdapter:
    if frontend == "tk":
        from docker_app_launcher import gui
        from tests.test_gui_window import _display_available, _keep_off_screen

        if not _display_available():
            pytest.skip("no display (run under xvfb-run)")
        window = gui.LauncherApp(_config())
        _keep_off_screen(window)
        window.update()

        def refresh_tk() -> None:
            window._refresh()
            window.update()

        return _WindowAdapter("tk", window, refresh_tk, window.destroy)
    if frontend == "ctk":
        from docker_app_launcher.frontends import ctk as ctk_frontend
        from tests.test_gui_window import _display_available, _keep_off_screen

        if not ctk_frontend.HAS_CTK:
            pytest.skip("customtkinter not installed")
        if not _display_available():
            pytest.skip("no display (run under xvfb-run)")
        ctk_window = ctk_frontend.CtkLauncherApp(_config())
        _keep_off_screen(ctk_window)
        ctk_window.update()

        def refresh_ctk() -> None:
            ctk_window._refresh()
            ctk_window.update()

        return _WindowAdapter("ctk", ctk_window, refresh_ctk, ctk_window.destroy)
    if frontend == "qt":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from docker_app_launcher.frontends import qt as qt_frontend

        if not qt_frontend.HAS_QT:
            pytest.skip("PySide6 not installed")
        from PySide6.QtWidgets import QApplication

        qapp = QApplication.instance() or QApplication([])
        qt_window = qt_frontend.QtLauncherApp(_config())
        qapp.processEvents()

        def refresh_qt() -> None:
            qt_window._refresh()
            qapp.processEvents()

        def close_qt() -> None:
            qt_window._stop_tray()
            qt_window.deleteLater()
            qapp.processEvents()

        return _WindowAdapter("qt", qt_window, refresh_qt, close_qt)
    raise AssertionError(f"unknown frontend {frontend!r}")


FRONTENDS = ["tk", "ctk", "qt"]


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_window_contract_for_every_known_state(frontend, matrix_state) -> None:
    """One window per frontend, driven through EVERY state in the central
    table - title, full button set, unique labels, exact enablement."""
    import docker_app_launcher

    adapter = _build_window(frontend, matrix_state)
    config = adapter.window._cfg
    try:
        # Title: real product name + the actually installed version.
        assert adapter.title().startswith(config.app_name)
        assert docker_app_launcher.__version__ in adapter.title()
        assert "My App" not in adapter.title()  # the placeholder from the README example

        for state in KNOWN_STATES:
            matrix_state["value"] = state
            adapter.refresh()

            labels = adapter.button_labels()
            assert set(labels) == set(ALL_BUTTONS), f"[{frontend}/{state}] button set out of sync"
            expected_labels = {name: i18n.t(ui_model.BUTTON_LABELS[name], config) for name in ALL_BUTTONS}
            assert labels == expected_labels, f"[{frontend}/{state}] labels drifted from the i18n catalog"
            values = list(labels.values())
            duplicates = {v for v in values if values.count(v) > 1}
            assert duplicates == set(), f"[{frontend}/{state}] identically labelled buttons: {duplicates}"

            for name in ALL_BUTTONS:
                assert adapter.button_enabled(name) == ui_model.button_enabled(state, name), (
                    f"[{frontend}/{state}] enablement of {name!r} diverges from BUTTON_STATES"
                )
            assert adapter.port_editable() == ui_model.port_editable(state), f"[{frontend}/{state}] port editability"
    finally:
        adapter.close()


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_multi_step_detection_streams_into_the_log(frontend, matrix_state, monkeypatch) -> None:
    """The transient offer buttons and the detection sub-steps must be
    VISIBLE, not just the end state (the screenshot-discovery class)."""

    def fake_detailed(cfg, *, on_step=None):
        if on_step is not None:
            on_step("Checking Docker context 'ctx-a' (unix:///a.sock)…")
            on_step("Checking Docker context 'ctx-b' (unix:///b.sock)…")
        return {
            "status": "not_running",
            "platform": "Linux",
            "detail": "not running",
            "command": "",
            "can_start": True,
            "installed": True,
            "can_fix_permission": False,
        }

    monkeypatch.setattr(actions, "check_docker_detailed", fake_detailed)
    adapter = _build_window(frontend, matrix_state)
    try:
        matrix_state["value"] = "no_docker"
        adapter.refresh()
        log = adapter.log_text()
        assert "ctx-a" in log and "ctx-b" in log, f"[{frontend}] detection steps missing from the visible log"
        # The first log line is the version line - part of the same contract.
        import docker_app_launcher

        assert docker_app_launcher.__version__ in log.splitlines()[0]
    finally:
        adapter.close()


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_no_duplicate_labels_anywhere_even_with_transient_buttons(frontend, matrix_state) -> None:
    """Scan EVERY button in the window, not just the fixed tables: the #33
    double-'cleanup' bug lived between a fixed and a TRANSIENT button."""
    adapter = _build_window(frontend, matrix_state)
    try:
        adapter.window._show_cleanup_offer({"containers": ["old-app"]})
        adapter.refresh()
        if frontend == "qt":
            from PySide6.QtWidgets import QPushButton

            labels = [str(b.text()) for b in adapter.window.findChildren(QPushButton)]
        else:
            labels = [str(b.cget("text")) for b in adapter.window._iter_buttons()]
        duplicates = {label for label in labels if labels.count(label) > 1}
        assert duplicates == set(), f"[{frontend}] identically labelled buttons with the offer open: {duplicates}"
    finally:
        adapter.close()
