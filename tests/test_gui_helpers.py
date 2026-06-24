"""Tests for the pure GUI helpers - no Tk window is created."""

from __future__ import annotations

import tkinter as tk
from typing import Any

import pytest

from docker_app_launcher import actions, gui
from docker_app_launcher.config import LauncherConfig


@pytest.fixture
def cfg() -> LauncherConfig:
    return LauncherConfig(app_name="X").resolve()


class TestPortEditable:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("not_installed", True),
            ("stopped", True),
            ("running", True),
            ("no_docker", False),
        ],
    )
    def test_states(self, state: str, expected: bool) -> None:
        assert gui.port_editable(state) is expected


class TestButtonsForState:
    def test_no_docker_has_recheck(self) -> None:
        assert gui.buttons_for_state("no_docker") == [("recheck", "retry")]

    def test_not_installed_has_install(self) -> None:
        assert ("install", "install") in gui.buttons_for_state("not_installed")

    def test_running_has_open_change_port_stop_uninstall(self) -> None:
        ids = [a for a, _ in gui.buttons_for_state("running")]
        assert ids == ["open", "change_port", "stop", "uninstall"]

    def test_stopped_has_start_uninstall(self) -> None:
        ids = [a for a, _ in gui.buttons_for_state("stopped")]
        assert ids == ["start", "uninstall"]

    def test_unknown_state_empty(self) -> None:
        assert gui.buttons_for_state("weird") == []


class TestDispatchAction:
    def test_install_routes_to_ensure_installed(self, cfg, monkeypatch) -> None:
        called: dict[str, object] = {}
        monkeypatch.setattr(actions, "ensure_installed", lambda c, **k: called.setdefault("v", (True, "done")))
        assert gui.dispatch_action("install", cfg) == (True, "done")
        assert "v" in called

    def test_start_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "start", lambda c, **k: (True, "started"))
        assert gui.dispatch_action("start", cfg) == (True, "started")

    def test_stop_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))
        assert gui.dispatch_action("stop", cfg) == (True, "stopped")

    def test_uninstall_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "uninstall", lambda c, **k: (True, "gone"))
        assert gui.dispatch_action("uninstall", cfg) == (True, "gone")

    def test_change_port_routes_with_port(self, cfg, monkeypatch) -> None:
        seen: dict[str, object] = {}

        def fake_change(c, p, **k):
            seen["port"] = p
            return (True, "ok")

        monkeypatch.setattr(actions, "change_port", fake_change)
        assert gui.dispatch_action("change_port", cfg, port=9000) == (True, "ok")
        assert seen["port"] == 9000

    def test_change_port_without_port_is_invalid(self, cfg) -> None:
        result = gui.dispatch_action("change_port", cfg)
        assert result is not None
        ok, msg = result
        assert ok is False and "between" in msg

    def test_open_returns_none(self, cfg, monkeypatch) -> None:
        opened: list[object] = []
        monkeypatch.setattr(actions, "open_browser", lambda c: opened.append(c))
        assert gui.dispatch_action("open", cfg) is None
        assert opened == [cfg]

    def test_recheck_returns_none(self, cfg) -> None:
        assert gui.dispatch_action("recheck", cfg) is None

    def test_unknown_returns_none(self, cfg) -> None:
        assert gui.dispatch_action("frobnicate", cfg) is None


class _FakeButton:
    """Stands in for a ``tk.Button``: supports ``btn["state"] = ...``."""

    def __init__(self) -> None:
        self.state = "normal"

    def __setitem__(self, key: str, value: str) -> None:
        assert key == "state"
        self.state = value


def _busy_app(monkeypatch: pytest.MonkeyPatch, buttons: list[_FakeButton]) -> tuple[gui.LauncherApp, dict[str, Any]]:
    """Build a LauncherApp without a real Tk window, with every Tk-touching
    method stubbed so ``_set_busy`` can be exercised headlessly."""
    app = gui.LauncherApp.__new__(gui.LauncherApp)
    app._cfg = LauncherConfig(app_name="X").resolve()
    calls: dict[str, Any] = {"attributes": [], "lift": 0, "focus_force": 0, "logged": 0, "cleared": 0}
    # ``_iter_buttons`` would walk a real widget tree; feed it our fakes instead
    # so the test does not need a window. The real ``_set_topmost`` /
    # ``_bring_to_front`` still run, calling the stubbed primitives below.
    monkeypatch.setattr(app, "_iter_buttons", lambda: buttons)
    monkeypatch.setattr(app, "attributes", lambda *a: calls["attributes"].append(a))
    monkeypatch.setattr(app, "lift", lambda: calls.__setitem__("lift", calls["lift"] + 1))
    monkeypatch.setattr(app, "focus_force", lambda: calls.__setitem__("focus_force", calls["focus_force"] + 1))
    monkeypatch.setattr(app, "_clear_status", lambda: calls.__setitem__("cleared", calls["cleared"] + 1))
    monkeypatch.setattr(app, "_log", lambda *a, **k: calls.__setitem__("logged", calls["logged"] + 1))
    return app, calls


class TestSetBusy:
    def test_all_buttons_disabled_during_action(self, monkeypatch) -> None:
        buttons = [_FakeButton(), _FakeButton(), _FakeButton()]
        app, _ = _busy_app(monkeypatch, buttons)
        app._set_busy(True)
        assert all(btn.state == "disabled" for btn in buttons)

    def test_all_buttons_enabled_after_action(self, monkeypatch) -> None:
        buttons = [_FakeButton(), _FakeButton()]
        app, _ = _busy_app(monkeypatch, buttons)
        app._set_busy(True)
        app._set_busy(False)
        assert all(btn.state == "normal" for btn in buttons)

    def test_topmost_set_while_busy(self, monkeypatch) -> None:
        app, calls = _busy_app(monkeypatch, [_FakeButton()])
        app._set_busy(True)
        assert ("-topmost", True) in calls["attributes"]
        # Busy must not steal focus repeatedly; front-raising happens on finish.
        assert calls["lift"] == 0 and calls["focus_force"] == 0

    def test_topmost_cleared_and_window_raised_after(self, monkeypatch) -> None:
        app, calls = _busy_app(monkeypatch, [_FakeButton()])
        app._set_busy(True)
        app._set_busy(False)
        assert calls["attributes"][-1] == ("-topmost", False)
        assert calls["lift"] == 1 and calls["focus_force"] == 1

    def test_busy_clears_and_logs_once(self, monkeypatch) -> None:
        app, calls = _busy_app(monkeypatch, [_FakeButton()])
        app._set_busy(True)
        assert calls["cleared"] == 1 and calls["logged"] == 1

    def test_topmost_tclerror_is_swallowed(self, monkeypatch) -> None:
        app, _ = _busy_app(monkeypatch, [_FakeButton()])

        def boom(*_a: object) -> None:
            raise tk.TclError("no WM")

        monkeypatch.setattr(app, "attributes", boom)
        # A window-manager quirk must never crash an action.
        app._set_busy(True)


class TestAdvancedPorts:
    def _cfg(self) -> LauncherConfig:
        return LauncherConfig(
            app_name="X",
            internal_ports={"backend": 8000, "nginx": 80},
            env_internal_port_keys={"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"},
            show_advanced_ports=True,
        ).resolve()

    def test_visible_only_when_opted_in_and_declared(self) -> None:
        assert gui.advanced_ports_visible(self._cfg()) is True
        off = LauncherConfig(app_name="X", show_advanced_ports=False).resolve()
        assert gui.advanced_ports_visible(off) is False
        # opted in but nothing declared -> still hidden
        empty = LauncherConfig(app_name="X", show_advanced_ports=True).resolve()
        assert gui.advanced_ports_visible(empty) is False

    def test_internal_port_fields_rows(self) -> None:
        rows = gui.internal_port_fields(self._cfg())
        names = [name for name, _, _ in rows]
        assert names == ["backend", "nginx"]  # sorted
        values = {name: value for name, _, value in rows}
        assert values == {"backend": 8000, "nginx": 80}
        assert all(label for _, label, _ in rows)

    def test_default_internal_ports(self) -> None:
        assert gui.default_internal_ports(self._cfg()) == {"backend": 8000, "nginx": 80}


class TestShouldMinimizeToTray:
    def test_running_with_tray(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=True, tray_enabled=True) is True

    def test_running_no_tray(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=False, tray_enabled=True) is False

    def test_running_tray_disabled(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=True, tray_enabled=False) is False

    def test_stopped_never_minimizes(self) -> None:
        assert gui.should_minimize_to_tray("stopped", tray_available=True, tray_enabled=True) is False
