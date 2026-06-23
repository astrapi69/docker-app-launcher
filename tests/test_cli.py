"""Tests for the CLI entry point and CLI<->GUI parity (no real GUI)."""

from __future__ import annotations

import pytest

from docker_app_launcher import __main__, __version__, actions, gui


class TestParser:
    def test_defaults(self) -> None:
        args = __main__.build_parser().parse_args([])
        assert args.config == "launcher.json"
        assert args.port is None

    def test_flags(self) -> None:
        args = __main__.build_parser().parse_args(["--install", "--port", "9000"])
        assert args.install is True and args.port == 9000


class TestVersion:
    def test_prints_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = __main__.main(["--version"])
        out = capsys.readouterr().out
        assert rc == 0 and __version__ in out


class TestCliActions:
    def test_check_ok(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "Docker is running."))
        rc = __main__.main(["--check"])
        assert rc == 0 and "running" in capsys.readouterr().out

    def test_check_fail(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        assert __main__.main(["--check"]) == 1

    def test_status(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        rc = __main__.main(["--status"])
        assert rc == 0 and "running" in capsys.readouterr().out

    def test_install_routes_through_actions(self, monkeypatch) -> None:
        seen: dict[str, object] = {}
        monkeypatch.setattr(actions, "install", lambda c, **k: seen.setdefault("v", (True, "ok")))
        assert __main__.main(["--install"]) == 0
        assert "v" in seen

    def test_install_failure_exit_code(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "install", lambda c, **k: (False, "bad"))
        assert __main__.main(["--install"]) == 1

    def test_stop_routes(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))
        assert __main__.main(["--stop"]) == 0

    def test_uninstall_routes(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "uninstall", lambda c, **k: (True, "gone"))
        assert __main__.main(["--uninstall"]) == 0

    def test_cleanup_routes(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda c: {"containers": []})
        monkeypatch.setattr(actions, "cleanup_stale", lambda c, s, **k: (True, "clean"))
        assert __main__.main(["--cleanup"]) == 0

    def test_open_routes(self, monkeypatch) -> None:
        opened: list[object] = []
        monkeypatch.setattr(actions, "open_browser", lambda c: opened.append(c))
        assert __main__.main(["--open"]) == 0 and len(opened) == 1


class TestPortFlag:
    def test_valid_port_persisted(self, monkeypatch) -> None:
        recorded = {}

        def fake_set_port(c, p):
            recorded["port"] = p
            return True, "set"

        monkeypatch.setattr(actions, "set_port", fake_set_port)
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        __main__.main(["--check", "--port", "9000"])
        assert recorded["port"] == 9000

    def test_invalid_port_returns_2(self, monkeypatch) -> None:
        assert __main__.main(["--port", "1"]) == 2


class TestGuiFallback:
    def test_no_action_launches_window(self, monkeypatch) -> None:
        launched: dict[str, object] = {}
        monkeypatch.setattr(gui, "run", lambda c, **k: launched.setdefault("v", 0) or 0)
        rc = __main__.main([])
        assert rc == 0 and "v" in launched
