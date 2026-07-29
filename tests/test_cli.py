"""Tests for the CLI entry point and CLI<->GUI parity (no real GUI)."""

from __future__ import annotations

import pytest

from docker_app_launcher import __main__, __version__, actions, lockfile
from docker_app_launcher.frontends import tk_window


class TestParser:
    def test_defaults(self) -> None:
        args = __main__.build_parser().parse_args([])
        # None = "not explicitly passed": main() then falls back to the
        # implicit launcher.json lookup, which stays fail-open (#32).
        assert args.config is None
        assert args.port is None

    def test_flags(self) -> None:
        args = __main__.build_parser().parse_args(["--install", "--port", "9000"])
        assert args.install is True and args.port == 9000


class TestExplicitConfigPath:
    """#32: an explicitly passed --config path that is missing is a
    deployment bug and must fail loudly, never launch an all-defaults GUI."""

    def test_missing_explicit_config_is_a_hard_error(self, tmp_path, capsys) -> None:
        rc = __main__.main(["--config", str(tmp_path / "nope.json"), "--check"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "nope.json" in err and "not found" in err

    def test_missing_explicit_config_never_reaches_the_action(self, tmp_path, monkeypatch) -> None:
        def must_not_run():
            raise AssertionError("action must not run with a broken --config")

        monkeypatch.setattr(actions, "check_docker", must_not_run)
        assert __main__.main(["--config", str(tmp_path / "nope.json"), "--check"]) == 2

    def test_implicit_default_stays_fail_open(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)  # no launcher.json here
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "Docker is running."))
        rc = __main__.main(["--check"])
        assert rc == 0
        assert "running" in capsys.readouterr().out

    def test_existing_explicit_config_is_loaded(self, tmp_path, monkeypatch, capsys) -> None:
        path = tmp_path / "launcher.json"
        path.write_text('{"app_name": "Cfg App"}', encoding="utf-8")
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        assert __main__.main(["--config", str(path), "--status"]) == 0


class TestLogLevelFlag:
    """P3: the log level is configurable per run, not only via config JSON."""

    def test_defaults_to_none(self) -> None:
        assert __main__.build_parser().parse_args([]).log_level is None

    def test_accepts_known_levels(self) -> None:
        assert __main__.build_parser().parse_args(["--log-level", "WARNING"]).log_level == "WARNING"

    def test_rejects_unknown_level(self, capsys) -> None:
        with pytest.raises(SystemExit):
            __main__.build_parser().parse_args(["--log-level", "CHATTY"])

    def test_overrides_config_log_level(self, monkeypatch, capsys) -> None:
        import logging

        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        __main__.main(["--log-level", "ERROR", "--check"])
        assert logging.getLogger().level == logging.ERROR

    def test_debug_flag_beats_log_level(self, monkeypatch) -> None:
        import logging

        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        __main__.main(["--log-level", "ERROR", "--debug", "--check"])
        assert logging.getLogger().level == logging.DEBUG


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
        # --status routes through the structured StatusReport since #86.
        from docker_app_launcher import doctor

        monkeypatch.setattr(doctor, "get_state", lambda c: "running")
        monkeypatch.setattr(doctor, "health_check", lambda c, port=None: (True, "healthy"))
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
        monkeypatch.setattr(tk_window, "run", lambda c, **k: launched.setdefault("v", 0) or 0)
        rc = __main__.main([])
        assert rc == 0 and "v" in launched


class TestSingleInstance:
    def test_second_instance_is_refused(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(lockfile, "another_instance_alive", lambda path: True)

        def boom(*a, **k):
            raise AssertionError("a second instance must not open a window")

        monkeypatch.setattr(tk_window, "run", boom)
        rc = __main__.main([])
        assert rc == 0
        assert "already running" in capsys.readouterr().out.lower()

    def test_second_instance_requests_focus(self, monkeypatch) -> None:
        # #31: the refusal also asks the RUNNING window to come forward.
        monkeypatch.setattr(lockfile, "another_instance_alive", lambda path: True)
        monkeypatch.setattr(tk_window, "run", lambda *a, **k: 0)
        requested: list[object] = []
        monkeypatch.setattr(lockfile, "request_focus", lambda path: requested.append(path))
        assert __main__.main([]) == 0
        assert len(requested) == 1

    def test_single_instance_false_skips_lockfile(self, monkeypatch, tmp_path) -> None:
        from docker_app_launcher.config import LauncherConfig

        cfg_path = tmp_path / "launcher.json"
        LauncherConfig(app_name="X", single_instance=False).to_json(cfg_path)

        def boom(*a, **k):
            raise AssertionError("lockfile must not be consulted when single_instance=False")

        monkeypatch.setattr(lockfile, "another_instance_alive", boom)
        launched: dict[str, object] = {}
        monkeypatch.setattr(tk_window, "run", lambda c, **k: launched.setdefault("v", 0) or 0)
        rc = __main__.main(["--config", str(cfg_path)])
        assert rc == 0 and "v" in launched

    def test_lock_is_written_during_run_and_cleared_after(self, monkeypatch) -> None:
        from docker_app_launcher.config import LauncherConfig

        state: dict[str, bool] = {}

        def fake_run(config, **k):
            state["existed_during"] = config.lock_path.is_file()
            return 0

        monkeypatch.setattr(tk_window, "run", fake_run)
        rc = __main__.main([])
        assert rc == 0
        assert state["existed_during"] is True
        cfg = LauncherConfig.from_json("launcher.json")
        assert not cfg.lock_path.is_file()


class TestStartRoute:
    def test_start_routes(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "start", lambda c, **k: (True, "started"))
        assert __main__.main(["--start"]) == 0

    def test_start_failure_exit_code(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "start", lambda c, **k: (False, "no compose file"))
        assert __main__.main(["--start"]) == 1


class TestRenderProbe:
    def test_probe_prints_contract_json(self, monkeypatch, capsys, tmp_path) -> None:
        import json

        import tests.test_gui_window as tgw

        if not tgw._display_available():
            pytest.skip("no display")
        config_file = tmp_path / "launcher.json"
        config_file.write_text(
            json.dumps(
                {
                    "app_name": "Probe App",
                    "locale": "de",
                    "single_instance": False,
                    "update_check_enabled": False,
                    "cleanup_on_start": False,
                }
            )
        )
        monkeypatch.setattr(actions, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(actions, "check_port", lambda p: (True, ""))
        assert __main__.main(["--config", str(config_file), "--render-probe"]) == 0
        contract = json.loads(capsys.readouterr().out)
        assert contract["title"].startswith("Probe App")
        assert contract["buttons"]["install"] == "Installieren"
        import docker_app_launcher

        assert docker_app_launcher.__version__ in contract["first_log_line"]
