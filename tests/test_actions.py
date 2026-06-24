"""Tests for the actions layer. No tkinter, no display, no real Docker.

Docker is mocked at ``actions._run`` or the higher-level helpers; ports use
real sockets; config + manifest use tmp dirs; health uses a mocked urlopen.
"""

from __future__ import annotations

import socket
import subprocess
import urllib.request
import webbrowser
from contextlib import contextmanager

import pytest

from docker_app_launcher import actions
from docker_app_launcher.config import LauncherConfig
from tests.conftest import make_result


def _bind_free_port() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


# --- check_docker / docker_installed --------------------------------------


class TestCheckDocker:
    def test_running(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="info"))
        ok, msg = actions.check_docker()
        assert ok is True and "running" in msg

    def test_not_installed(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok, msg = actions.check_docker()
        assert ok is False and "not installed" in msg

    def test_daemon_stopped(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(returncode=1, stderr="cannot connect"))
        ok, msg = actions.check_docker()
        assert ok is False and "not started" in msg

    def test_timeout(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=10)

        monkeypatch.setattr(actions, "_run", boom)
        ok, msg = actions.check_docker()
        assert ok is False and "not responding" in msg

    def test_docker_installed_true(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="Docker version 27"))
        ok, msg = actions.docker_installed()
        assert ok is True and "27" in msg

    def test_docker_installed_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok, _ = actions.docker_installed()
        assert ok is False


# --- get_state ------------------------------------------------------------


class TestGetState:
    def test_no_docker(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        assert actions.get_state(config) == "no_docker"

    def test_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        assert actions.get_state(config) == "not_installed"

    def test_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: ["c1"])
        assert actions.get_state(config) == "running"

    def test_stopped(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(
            actions, "_project_container_ids", lambda c, *, running_only: [] if running_only else ["c1"]
        )
        assert actions.get_state(config) == "stopped"

    def test_uses_config_filters(self, config, monkeypatch) -> None:
        seen = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            return make_result(stdout="")

        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_run", fake_run)
        actions.get_state(config)
        assert "name=test-app" in seen["cmd"]


# --- ports ----------------------------------------------------------------


class TestPorts:
    def test_check_port_free(self) -> None:
        ok, msg = actions.check_port(59999)
        assert ok is True and "free" in msg

    def test_check_port_occupied(self) -> None:
        sock, port = _bind_free_port()
        try:
            ok, msg = actions.check_port(port)
            assert ok is False and "occupied" in msg
        finally:
            sock.close()

    def test_check_port_too_low(self) -> None:
        ok, _ = actions.check_port(80)
        assert ok is False

    def test_check_port_too_high(self) -> None:
        ok, _ = actions.check_port(70000)
        assert ok is False

    def test_check_port_rejects_bool(self) -> None:
        ok, _ = actions.check_port(True)
        assert ok is False

    def test_find_free_port_finds_self(self) -> None:
        found, port, _ = actions.find_free_port(59000)
        assert found is True and port >= 59000

    def test_find_free_port_invalid_start(self) -> None:
        found, port, _ = actions.find_free_port(10)
        assert found is False and port == 0

    def test_find_free_port_skips_occupied(self) -> None:
        sock, port = _bind_free_port()
        try:
            found, got, _ = actions.find_free_port(port, max_tries=5)
            assert found is True and got != port
        finally:
            sock.close()


# --- port persistence -----------------------------------------------------


class TestPortPersistence:
    def test_set_and_resolve(self, config) -> None:
        ok, msg = actions.set_port(config, 9000)
        assert ok is True and "9000" in msg
        assert actions.resolve_port(config) == 9000

    def test_set_invalid_rejected(self, config) -> None:
        ok, _ = actions.set_port(config, 1)
        assert ok is False

    def test_resolve_default_when_unset(self, config) -> None:
        assert actions.resolve_port(config) == 8080

    def test_resolve_cli_port_wins(self, config) -> None:
        actions.set_port(config, 9000)
        assert actions.resolve_port(config, cli_port=9100) == 9100

    def test_resolve_ignores_invalid_cli(self, config) -> None:
        actions.set_port(config, 9000)
        assert actions.resolve_port(config, cli_port=1) == 9000

    def test_set_port_writes_env(self, config) -> None:
        actions.set_port(config, 9000)
        env = actions._env_path(config)
        assert env is not None and env.is_file()
        assert "APP_PORT=9000" in env.read_text()

    def test_set_port_upserts_env(self, config) -> None:
        actions.set_port(config, 9000)
        actions.set_port(config, 9100)
        env = actions._env_path(config)
        assert env is not None
        text = env.read_text()
        assert "APP_PORT=9100" in text and "APP_PORT=9000" not in text

    def test_write_env_port_without_install_dir(self, tmp_path) -> None:
        # Regression (Bug 1): with no install_dir the .env must STILL be written,
        # next to the compose file, so `docker compose` actually sees the new
        # port. Previously _env_path returned None and the write was a silent
        # no-op, so the launcher and Compose disagreed on the port.
        compose = tmp_path / "docker-compose.prod.yml"
        compose.write_text("services: {}\n")
        cfg = LauncherConfig(app_name="X", compose_file=str(compose), config_dir=str(tmp_path / ".x")).resolve()
        assert cfg.install_dir == ""
        actions._write_env_port(cfg, 9000)
        env = tmp_path / ".env"
        assert env.is_file() and "APP_PORT=9000" in env.read_text()

    def test_load_config_missing(self, tmp_path) -> None:
        assert actions.load_config(tmp_path / "no.json") == {}

    def test_set_and_resolve_locale(self, config) -> None:
        actions.set_locale(config, "fr")
        assert actions.resolve_locale(config) == "fr"

    def test_resolve_locale_defaults_to_config(self, config) -> None:
        # the config fixture pins locale="en"
        assert actions.resolve_locale(config) == "en"

    def test_resolve_locale_unknown_falls_back_en(self, config) -> None:
        actions.save_config(config.launcher_config_file, {"locale": "zz"})
        assert actions.resolve_locale(config) == "en"

    def test_save_load_round_trip(self, tmp_path) -> None:
        path = tmp_path / "c.json"
        actions.save_config(path, {"port": 1234})
        assert actions.load_config(path) == {"port": 1234}


# --- install --------------------------------------------------------------


def _make_repo(config: LauncherConfig) -> None:
    config.compose_path.parent.mkdir(parents=True, exist_ok=True)
    config.compose_path.write_text("services: {}\n")


class TestDockerBuildProgress:
    def _collect(self, lines: list[str], **kw) -> list[tuple[int, str]]:
        reports: list[tuple[int, str]] = []
        parser = actions.DockerBuildProgress(lambda pct, label: reports.append((pct, label)), **kw)
        for line in lines:
            parser.parse_line(line)
        return reports

    def test_estimated_total_gives_smooth_percent(self) -> None:
        reports = self._collect(["#5 [frontend 1/6] FROM node", "#20 [backend 5/9] RUN poetry"], estimated_total=40)
        assert reports[0][0] == 12  # 5/40
        assert reports[1][0] == 50  # 20/40

    def test_auto_detect_uses_max_step(self) -> None:
        reports = self._collect(["#10 [a 1/2] x", "#5 [b 1/1] y"])
        assert reports[0][0] == 99  # 10/10 -> capped at 99
        assert reports[1][0] == 50  # 5/10

    def test_cached_lines_count(self) -> None:
        assert self._collect(["#3 [a] CACHED"], estimated_total=10) == [(30, "#3 [a] CACHED")]

    def test_unknown_line_no_report_no_crash(self) -> None:
        assert self._collect(["building...", "Sending build context to Docker daemon"], estimated_total=10) == []

    def test_never_exceeds_99(self) -> None:
        assert self._collect(["#50 [x] y"], estimated_total=10)[0][0] == 99


class TestInstall:
    def test_success(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = actions.install(config)
        assert ok is True and "ready" in msg
        assert config.manifest_path.is_file()

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        ok, msg = actions.install(config)
        assert ok is False and "not available" in msg

    def test_already_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        ok, msg = actions.install(config)
        assert ok is True and "already installed" in msg

    def test_missing_compose(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "not_installed")
        ok, msg = actions.install(config)
        assert ok is False and "Compose" in msg

    def test_port_occupied(self, config, monkeypatch) -> None:
        _make_repo(config)
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(actions, "check_port", lambda p, **k: (False, "busy"))
        ok, msg = actions.install(config)
        assert ok is False and "occupied" in msg

    def test_build_failure(self, config, monkeypatch) -> None:
        _make_repo(config)
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(actions, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (1, "boom"))
        ok, msg = actions.install(config)
        assert ok is False and "boom" in msg

    def test_unhealthy(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (False, "no route"))
        ok, msg = actions.install(config)
        assert ok is False and "not reachable" in msg

    def test_on_step_called(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        steps: list[str] = []
        actions.install(config, on_step=steps.append)
        assert any("Building" in s for s in steps)

    def test_on_progress_reaches_0_and_100_with_indeterminate_health(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        pcts: list[int | None] = []
        actions.install(config, on_progress=lambda pct, label: pcts.append(pct))
        assert pcts[0] == 0
        assert pcts[-1] == 100
        assert None in pcts  # indeterminate during the health check


# --- start / stop ---------------------------------------------------------


class TestStart:
    def test_success(self, config, monkeypatch) -> None:
        states = iter(["stopped", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = actions.start(config)
        assert ok is True and "started" in msg

    def test_already_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        ok, msg = actions.start(config)
        assert ok is True and "already running" in msg

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        ok, _ = actions.start(config)
        assert ok is False

    def test_compose_failure(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "stopped")
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (1, "fail"))
        ok, msg = actions.start(config)
        assert ok is False and "fail" in msg

    def test_no_container_after(self, config, monkeypatch) -> None:
        states = iter(["stopped", "stopped"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        ok, _ = actions.start(config)
        assert ok is False


class TestStop:
    def test_success(self, config, monkeypatch) -> None:
        states = iter(["running", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [] if running_only else ["c"])
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result())
        ok, msg = actions.stop(config)
        assert ok is True and "stopped" in msg

    def test_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "not_installed")
        ok, _ = actions.stop(config)
        assert ok is False

    def test_already_stopped(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "stopped")
        ok, msg = actions.stop(config)
        assert ok is True and "already" in msg

    def test_verify_still_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: ["c"])
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result())
        ok, _ = actions.stop(config)
        assert ok is False


# --- change_port (public host-port change) --------------------------------


class TestChangePort:
    def test_invalid_port_rejected(self, config) -> None:
        ok, _ = actions.change_port(config, 1)
        assert ok is False

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        ok, msg = actions.change_port(config, 9000)
        assert ok is False and "not available" in msg

    def test_not_running_only_persists(self, config, monkeypatch) -> None:
        # Stack stopped -> persist the port (a later start picks it up), do NOT
        # touch Compose. resolve_port reflects the new value afterwards.
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "stopped")
        ok, _ = actions.change_port(config, 9000)
        assert ok is True
        assert actions.resolve_port(config) == 9000

    def test_running_stop_restart_healthcheck(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["running", "running"])  # initial probe, then post-restart
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = actions.change_port(config, 9000)
        assert ok is True and "9000" in msg
        assert actions.resolve_port(config) == 9000

    def test_restart_uses_no_build(self, config, monkeypatch) -> None:
        # A public-port change must recreate WITHOUT --build (seconds, not the
        # minutes a rebuild costs). The internal-port rebuild path is separate.
        captured: dict[str, tuple[str, ...]] = {}
        states = iter(["running", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))

        def fake_stream(c, *args, **kwargs):
            captured["args"] = args
            return (0, "")

        monkeypatch.setattr(actions, "_stream_compose", fake_stream)
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        actions.change_port(config, 9000)
        assert captured["args"] == ("up", "-d")
        assert "--build" not in captured["args"]

    def test_stop_failure_aborts(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        monkeypatch.setattr(actions, "stop", lambda c: (False, "cannot stop"))
        ok, msg = actions.change_port(config, 9000)
        assert ok is False and "cannot stop" in msg

    def test_unhealthy_after_restart(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["running", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (False, "no route"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = actions.change_port(config, 9000)
        assert ok is False and "not reachable" in msg

    def test_health_check_targets_new_port(self, config, monkeypatch) -> None:
        _make_repo(config)
        seen: dict[str, int] = {}
        states = iter(["running", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(actions, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))

        def fake_health(c, port=None):
            seen["port"] = port
            return (True, "ok")

        monkeypatch.setattr(actions, "health_check", fake_health)
        actions.change_port(config, 9000)
        assert seen["port"] == 9000


# --- internal (container) ports -------------------------------------------


@pytest.fixture
def iconfig(config):
    """A config that declares two internal ports (backend + nginx)."""
    config.internal_ports = {"backend": 8000, "nginx": 80}
    config.env_internal_port_keys = {"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"}
    config.show_advanced_ports = True
    return config


class TestInternalPorts:
    def test_validate_allows_low_ports(self) -> None:
        # Internal ports are not host-published, so 80 is valid (unlike a host port).
        assert actions._validate_internal_port(80)[0] is True
        assert actions._validate_internal_port(0)[0] is False
        assert actions._validate_internal_port(70000)[0] is False

    def test_resolve_default_from_config(self, iconfig) -> None:
        assert actions.resolve_internal_port(iconfig, "backend") == 8000
        assert actions.resolve_internal_port(iconfig, "nginx") == 80

    def test_resolve_override_wins(self, iconfig) -> None:
        actions.set_internal_port(iconfig, "backend", 9001)
        assert actions.resolve_internal_port(iconfig, "backend") == 9001

    def test_resolve_invalid_override_ignored(self, iconfig) -> None:
        actions.save_config(iconfig.launcher_config_file, {"internal_ports": {"backend": 70000}})
        assert actions.resolve_internal_port(iconfig, "backend") == 8000

    def test_set_unknown_name_rejected(self, iconfig) -> None:
        ok, msg = actions.set_internal_port(iconfig, "db", 5432)
        assert ok is False and "db" in msg

    def test_set_persists_and_writes_env(self, iconfig) -> None:
        ok, _ = actions.set_internal_port(iconfig, "backend", 9001)
        assert ok is True
        env = actions._env_path(iconfig).read_text()
        assert "APP_BACKEND_PORT=9001" in env

    def test_write_env_ports_writes_all_ports(self, iconfig) -> None:
        # The .env write self-creates its parent dir, so no repo scaffolding needed.
        actions._write_env_ports(iconfig)
        env = actions._env_path(iconfig).read_text()
        assert f"{iconfig.env_port_key}=" in env
        assert "APP_BACKEND_PORT=8000" in env
        assert "APP_NGINX_PORT=80" in env

    def test_change_unknown_name_rejected(self, iconfig, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        ok, _ = actions.change_internal_port(iconfig, "db", 5432)
        assert ok is False

    def test_change_invalid_port_rejected(self, iconfig) -> None:
        ok, _ = actions.change_internal_port(iconfig, "backend", 0)
        assert ok is False

    def test_change_not_running_only_persists(self, iconfig, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "stopped")
        ok, _ = actions.change_internal_port(iconfig, "backend", 9001)
        assert ok is True
        assert actions.resolve_internal_port(iconfig, "backend") == 9001

    def test_change_running_rebuilds(self, iconfig, monkeypatch) -> None:
        # An internal-port change MUST rebuild (up --build -d), not just restart.
        _make_repo(iconfig)
        captured: dict[str, tuple[str, ...]] = {}
        states = iter(["running", "running"])
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: next(states))
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))

        def fake_stream(c, *args, **kwargs):
            captured["args"] = args
            return (0, "")

        monkeypatch.setattr(actions, "_stream_compose", fake_stream)
        monkeypatch.setattr(actions, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = actions.change_internal_port(iconfig, "backend", 9001)
        assert ok is True and "9001" in msg
        assert captured["args"] == ("up", "--build", "-d")

    def test_change_stop_failure_aborts(self, iconfig, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "get_state", lambda c: "running")
        monkeypatch.setattr(actions, "stop", lambda c: (False, "cannot stop"))
        ok, msg = actions.change_internal_port(iconfig, "backend", 9001)
        assert ok is False and "cannot stop" in msg


# --- uninstall ------------------------------------------------------------


class TestUninstall:
    def test_success_verbose(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_project_containers", lambda c, *, running_only: [("c1", "test-app")])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        monkeypatch.setattr(actions, "_docker_op", lambda cmd, **k: (True, ""))
        monkeypatch.setattr(actions, "_project_images", lambda c: [])
        steps: list[str] = []
        ok, msg = actions.uninstall(config, on_step=steps.append)
        assert ok is True and "preserved" in msg
        assert any("test-app" in s and "✓" in s for s in steps)

    def test_nothing_to_uninstall(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_project_containers", lambda c, *, running_only: [])
        monkeypatch.setattr(actions, "_project_images", lambda c: [])
        ok, msg = actions.uninstall(config)
        assert ok is True and "Nothing to uninstall" in msg

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        ok, _ = actions.uninstall(config)
        assert ok is False

    def test_partial_failure(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_project_containers", lambda c, *, running_only: [("c1", "test-app")])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: ["c1"])
        monkeypatch.setattr(actions, "_docker_op", lambda cmd, **k: (False, "denied"))
        ok, msg = actions.uninstall(config)
        assert ok is False and "could not be removed" in msg


# --- health + browser -----------------------------------------------------


@contextmanager
def _fake_response(status: int, body: str):
    class _Resp:
        def __init__(self) -> None:
            self.status = status

        def read(self) -> bytes:
            return body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    yield _Resp()


class TestHealth:
    def test_healthy_json(self, config, monkeypatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(200, '{"status": "ok"}'))
        assert actions.is_healthy(config, 8080) is True

    def test_status_mismatch(self, config, monkeypatch) -> None:
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(200, '{"status": "bad"}')
        )
        assert actions.is_healthy(config, 8080) is False

    def test_non_200(self, config, monkeypatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(503, ""))
        assert actions.is_healthy(config, 8080) is False

    def test_no_key_means_200_is_enough(self, monkeypatch) -> None:
        cfg = LauncherConfig(app_name="X", health_check_key="").resolve()
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(200, "hi"))
        assert actions.is_healthy(cfg, 8080) is True

    def test_connection_error(self, config, monkeypatch) -> None:
        def boom(url, timeout=3.0):
            raise OSError("refused")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        assert actions.is_healthy(config, 8080) is False

    def test_health_check_times_out(self, config, monkeypatch) -> None:
        cfg = config
        cfg.health_check_timeout = 0
        monkeypatch.setattr(actions, "_health_probe", lambda c, p: (False, "nope"))
        ok, msg = actions.health_check(cfg, 8080)
        assert ok is False and "not reachable" in msg

    def test_open_browser_uses_browser_path(self, monkeypatch) -> None:
        cfg = LauncherConfig(app_name="X", browser_path="/dashboard").resolve()
        opened: list[str] = []
        monkeypatch.setattr(webbrowser, "open", opened.append)
        actions.open_browser(cfg, 8080)
        assert opened == ["http://localhost:8080/dashboard"]

    def test_open_browser_never_raises(self, config, monkeypatch) -> None:
        def boom(url):
            raise OSError("no browser")

        monkeypatch.setattr(webbrowser, "open", boom)
        actions.open_browser(config, 8080)  # should not raise


# --- manifest -------------------------------------------------------------


class TestManifest:
    def test_read_missing(self, config) -> None:
        assert actions.read_manifest(config) is None

    def test_write_and_read(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        actions.write_manifest(config, "1.2.3")
        data = actions.read_manifest(config)
        assert data is not None and data["app_version"] == "1.2.3"
        assert data["app_name"] == "Test App"

    def test_append_history(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        actions.write_manifest(config, "1.0.0")
        actions.append_history(config, "install", "1.0.0")
        data = actions.read_manifest(config)
        assert data is not None and data["install_history"][-1]["action"] == "install"

    def test_get_version_from_manifest(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        actions.write_manifest(config, "9.9.9")
        assert actions.get_version(config) == "9.9.9"

    def test_mark_uninstalled_clears_artifacts(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        actions.write_manifest(config, "1.0.0")
        actions.mark_uninstalled(config, "1.0.0")
        data = actions.read_manifest(config)
        assert data is not None and data["status"] == "uninstalled"
        assert data["containers"] == []

    def test_manifest_artifacts_excluded_after_uninstall(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=""))
        actions.write_manifest(config, "1.0.0")
        actions.mark_uninstalled(config, "1.0.0")
        arts = actions.manifest_artifacts(config)
        assert arts == {"containers": [], "images": [], "volumes": [], "configs": []}


# --- cleanup --------------------------------------------------------------


class TestCleanup:
    def test_has_stale_artifacts(self) -> None:
        assert actions.has_stale_artifacts({"containers": ["x"]}) is True
        assert actions.has_stale_artifacts({"containers": [], "images": []}) is False

    def test_cleanup_offer_lines(self, config) -> None:
        lines = actions.cleanup_offer_lines(config, {"containers": ["a", "b"], "images": ["i:1"]})
        assert any("2 Container" in line for line in lines)
        assert any("Image(s)" in line for line in lines)

    def test_find_stale_excludes_active(self, config, monkeypatch) -> None:
        monkeypatch.setattr(
            actions,
            "manifest_artifacts",
            lambda c: {"containers": ["test-app"], "images": [], "volumes": [], "configs": []},
        )
        monkeypatch.setattr(
            actions, "_docker_names", lambda c, kind, pats: ["test-app", "old-app"] if kind == "container" else []
        )
        monkeypatch.setattr(actions, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        stale = actions.find_stale_artifacts(config)
        assert stale["containers"] == ["old-app"]

    def test_find_stale_config_dirs(self, config, tmp_path, monkeypatch) -> None:
        legacy = tmp_path / "legacy-config"
        legacy.mkdir()
        config.cleanup_configs = [str(legacy)]
        monkeypatch.setattr(
            actions, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(actions, "_docker_names", lambda c, kind, pats: [])
        monkeypatch.setattr(actions, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(actions, "_running_container_names", lambda c: [])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        stale = actions.find_stale_artifacts(config)
        assert str(legacy) in stale["configs"]

    def test_find_stale_searches_cleanup_search_paths(self, config, tmp_path, monkeypatch) -> None:
        # cleanup_search_paths scans base dirs for legacy_names subdirs (both
        # "<base>/<name>" and the dotted "<base>/.<name>").
        base = tmp_path / "base"
        (base / ".oldapp").mkdir(parents=True)
        (base / "oldapp").mkdir()
        config.legacy_names = ["oldapp"]
        config.cleanup_search_paths = [str(base)]
        config.cleanup_configs = []
        monkeypatch.setattr(
            actions, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(actions, "_docker_names", lambda c, kind, pats: [])
        monkeypatch.setattr(actions, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(actions, "_running_container_names", lambda c: [])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        stale = actions.find_stale_artifacts(config)
        assert str(base / ".oldapp") in stale["configs"]
        assert str(base / "oldapp") in stale["configs"]

    def test_find_stale_search_skips_missing_and_live_config(self, config, tmp_path, monkeypatch) -> None:
        base = tmp_path / "base"
        base.mkdir()  # no legacy subdir exists -> nothing found
        config.legacy_names = ["ghost"]
        config.cleanup_search_paths = [str(base)]
        monkeypatch.setattr(
            actions, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(actions, "_docker_names", lambda c, kind, pats: [])
        monkeypatch.setattr(actions, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(actions, "_running_container_names", lambda c: [])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        assert actions.find_stale_artifacts(config)["configs"] == []

    def _stale_volumes_setup(self, monkeypatch, volumes: list[str]) -> None:
        monkeypatch.setattr(
            actions, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(actions, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(actions, "_running_container_names", lambda c: [])
        monkeypatch.setattr(actions, "_docker_names", lambda c, kind, pats: volumes if kind == "volume" else [])

    def test_find_stale_protects_active_project_volume(self, config, monkeypatch) -> None:
        # The active project's own volume (<project>_*) is NEVER offered; legacy
        # volumes still are - regardless of whether containers currently exist.
        self._stale_volumes_setup(monkeypatch, ["test-app_test-app-data", "bibliogon_bibliogon-data"])
        stale = actions.find_stale_artifacts(config)
        assert "test-app_test-app-data" not in stale["volumes"]
        assert "bibliogon_bibliogon-data" in stale["volumes"]

    def test_find_stale_protects_project_volume_even_without_containers(self, config, monkeypatch) -> None:
        # Unconditional: even with no containers (cleanup runs at startup), the
        # active project's data volume is not offered for deletion.
        self._stale_volumes_setup(monkeypatch, ["test-app_test-app-data"])
        monkeypatch.setattr(actions, "_project_container_ids", lambda c, *, running_only: [])
        assert actions.find_stale_artifacts(config)["volumes"] == []

    def test_cleanup_stale_removes_and_reports(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_docker_op", lambda cmd, **k: (True, ""))
        monkeypatch.setattr(actions, "_image_size_bytes", lambda ref: 245_000_000)
        steps: list[str] = []
        ok, msg = actions.cleanup_stale(
            config, {"containers": ["old"], "images": ["i:1"], "volumes": [], "configs": []}, on_step=steps.append
        )
        assert ok is True and "2 artifact" in msg
        assert any("245 MB" in s for s in steps)

    def test_cleanup_stale_skips_volumes_by_default(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        removed: list[list[str]] = []

        def fake_op(cmd, **k):
            removed.append(cmd)
            return True, ""

        monkeypatch.setattr(actions, "_docker_op", fake_op)
        actions.cleanup_stale(config, {"containers": [], "images": [], "volumes": ["v1"], "configs": []})
        assert not any("volume" in cmd for cmd in removed)

    def test_cleanup_logs_every_skipped_volume(self, config, monkeypatch) -> None:
        # No silent gaps: unselected volumes AND active-project volumes each get a line.
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(actions, "_docker_op", lambda cmd, **k: (True, ""))
        monkeypatch.setattr(actions, "_project_volumes", lambda c: ["test-app_test-app-data"])
        steps: list[str] = []
        actions.cleanup_stale(
            config,
            {"containers": [], "images": [], "volumes": ["bibliogon_bibliogon-data"], "configs": []},
            on_step=steps.append,
        )
        assert any("bibliogon_bibliogon-data" in s and "not selected" in s for s in steps)
        assert any("test-app_test-app-data" in s and "active project" in s for s in steps)

    def test_cleanup_stale_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "check_docker", lambda: (False, "down"))
        ok, _ = actions.cleanup_stale(config, {"containers": ["x"]})
        assert ok is False

    def test_cleanup_removes_config_dir(self, config, tmp_path, monkeypatch) -> None:
        target = tmp_path / "stale-cfg"
        target.mkdir()
        monkeypatch.setattr(actions, "check_docker", lambda: (True, "ok"))
        actions.cleanup_stale(config, {"containers": [], "images": [], "volumes": [], "configs": [str(target)]})
        assert not target.exists()


# --- human size -----------------------------------------------------------


@pytest.mark.parametrize(
    ("num", "expected"),
    [(0, "0 B"), (500, "500 B"), (2_000, "2 KB"), (245_000_000, "245 MB"), (3_000_000_000, "3 GB")],
)
def test_human_size(num: int, expected: str) -> None:
    assert actions._human_size(num) == expected
