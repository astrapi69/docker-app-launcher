"""Tests for the pure GUI helpers - no Tk window is created."""

from __future__ import annotations

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
            ("running", False),
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

    def test_running_has_open_stop_uninstall(self) -> None:
        ids = [a for a, _ in gui.buttons_for_state("running")]
        assert ids == ["open", "stop", "uninstall"]

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

    def test_open_returns_none(self, cfg, monkeypatch) -> None:
        opened: list[object] = []
        monkeypatch.setattr(actions, "open_browser", lambda c: opened.append(c))
        assert gui.dispatch_action("open", cfg) is None
        assert opened == [cfg]

    def test_recheck_returns_none(self, cfg) -> None:
        assert gui.dispatch_action("recheck", cfg) is None

    def test_unknown_returns_none(self, cfg) -> None:
        assert gui.dispatch_action("frobnicate", cfg) is None


class TestShouldMinimizeToTray:
    def test_running_with_tray(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=True, tray_enabled=True) is True

    def test_running_no_tray(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=False, tray_enabled=True) is False

    def test_running_tray_disabled(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=True, tray_enabled=False) is False

    def test_stopped_never_minimizes(self) -> None:
        assert gui.should_minimize_to_tray("stopped", tray_available=True, tray_enabled=True) is False
