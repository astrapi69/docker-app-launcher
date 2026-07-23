"""Tests for the actions layer. No tkinter, no display, no real Docker.

Docker is mocked at ``actions._run`` or the higher-level helpers; ports use
real sockets; config + manifest use tmp dirs; health uses a mocked urlopen.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import threading
import urllib.request
import webbrowser
from collections.abc import Iterator
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


# --- platform-specific docker check ---------------------------------------


class TestCheckDockerDetailed:
    def _patch(self, monkeypatch, system, which, info) -> None:
        monkeypatch.setattr("platform.system", lambda: system)
        monkeypatch.setattr("shutil.which", lambda _x: which)
        monkeypatch.setattr(actions, "_docker_info_rc", lambda extra_env=None: info)
        monkeypatch.setattr(actions, "_docker_contexts", lambda: [])

    def test_linux_not_installed(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", None, (127, ""))
        r = actions.check_docker_detailed(config)
        assert r["installed"] is False and "apt install" in r["command"] and r["platform"] == "Linux"

    def test_linux_daemon_off_offers_start(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", "/usr/bin/docker", (1, "Cannot connect to the Docker daemon"))
        r = actions.check_docker_detailed(config)
        assert r["installed"] and not r["running"] and r["can_start"] and "systemctl start docker" in r["command"]

    def test_linux_permission_denied(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", "/usr/bin/docker", (1, "permission denied while trying to connect"))
        r = actions.check_docker_detailed(config)
        assert "usermod -aG docker" in r["command"] and not r["running"]

    def test_linux_running(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", "/usr/bin/docker", (0, ""))
        assert actions.check_docker_detailed(config)["running"] is True

    def test_windows_desktop_installed_not_in_path(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Windows", None, (127, ""))
        monkeypatch.setattr("os.path.exists", lambda _p: True)
        r = actions.check_docker_detailed(config)
        assert r["installed"] and r["can_start"]

    def test_windows_not_installed(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Windows", None, (127, ""))
        monkeypatch.setattr("os.path.exists", lambda _p: False)
        assert actions.check_docker_detailed(config)["installed"] is False

    def test_darwin_app_present_not_running(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Darwin", "/usr/local/bin/docker", (1, "Cannot connect"))
        r = actions.check_docker_detailed(config)
        assert r["installed"] and r["can_start"]

    def test_never_raises_on_unknown_platform(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Plan9", None, (127, ""))
        monkeypatch.setattr("os.path.exists", lambda _p: False)
        assert actions.check_docker_detailed(config)["installed"] is False

    def test_install_url_override(self, config, monkeypatch) -> None:
        config.docker_install_url = "https://corp/docker"
        self._patch(monkeypatch, "Linux", None, (127, ""))
        assert actions.check_docker_detailed(config)["install_url"] == "https://corp/docker"

    def test_start_docker_daemon_success(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda cmd, **k: make_result(returncode=0))
        assert actions.start_docker_daemon()[0] is True

    def test_start_docker_desktop_not_found(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setattr("os.path.exists", lambda _p: False)
        assert actions.start_docker_desktop(config)[0] is False


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


# --- context-aware docker detection (#25) ----------------------------------


class TestDockerContextFallback:
    """The active context's probe failing must trigger a sweep over the
    other contexts (Docker Desktop for Linux / rootless setups) and, on a
    hit, CONNECT through that endpoint for every later docker command."""

    U_DEFAULT = "unix:///var/run/docker.sock"
    U_DESKTOP = "unix:///home/u/.docker/desktop/docker.sock"

    def _patch(self, monkeypatch, *, active_info, contexts, per_endpoint=None) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")

        def info_rc(extra_env=None):
            if extra_env and per_endpoint is not None:
                return per_endpoint.get(extra_env.get("DOCKER_HOST"), (1, "dead"))
            return active_info

        monkeypatch.setattr(actions, "_docker_info_rc", info_rc)
        monkeypatch.setattr(actions, "_docker_contexts", lambda: contexts)

    def test_falls_back_to_other_context_and_connects(self, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"),
            contexts=[("default", self.U_DEFAULT, True), ("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={self.U_DESKTOP: (0, "")},
        )
        ok, msg = actions.check_docker()
        assert ok is True
        assert "desktop-linux" in msg
        assert actions.docker_host_override() == self.U_DESKTOP

    def test_detailed_reports_fallback_context(self, config, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect"),
            contexts=[("default", self.U_DEFAULT, True), ("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={self.U_DESKTOP: (0, "")},
        )
        r = actions.check_docker_detailed(config)
        assert r["running"] is True
        assert "desktop-linux" in r["detail"]

    def test_detail_names_context_endpoint_and_docker_error(self, config, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"),
            contexts=[("default", self.U_DEFAULT, True)],
        )
        r = actions.check_docker_detailed(config)
        assert r["running"] is False and r["can_start"] is True
        assert "default" in r["detail"]
        assert self.U_DEFAULT in r["detail"]
        assert "Cannot connect to the Docker daemon" in r["detail"]

    def test_permission_denied_is_not_swept(self, config, monkeypatch) -> None:
        def contexts_must_not_be_called():
            raise AssertionError("permission failures must not trigger the context sweep")

        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(
            actions, "_docker_info_rc", lambda extra_env=None: (1, "permission denied while connecting")
        )
        monkeypatch.setattr(actions, "_docker_contexts", contexts_must_not_be_called)
        r = actions.check_docker_detailed(config)
        assert "usermod -aG docker" in r["command"] and not r["running"]

    def test_all_contexts_dead_stays_not_running(self, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect"),
            contexts=[("default", self.U_DEFAULT, True), ("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={},
        )
        ok, _ = actions.check_docker()
        assert ok is False
        assert actions.docker_host_override() is None

    def test_active_context_ok_needs_no_sweep(self, monkeypatch) -> None:
        def contexts_must_not_be_called():
            raise AssertionError("a healthy active context must not trigger the sweep")

        monkeypatch.setattr(actions, "_docker_info_rc", lambda extra_env=None: (0, ""))
        monkeypatch.setattr(actions, "_docker_contexts", contexts_must_not_be_called)
        ok, msg = actions.check_docker()
        assert ok is True and msg == "Docker is running."
        assert actions.docker_host_override() is None

    def test_override_injected_into_subsequent_runs(self, monkeypatch) -> None:
        seen_env = {}

        def fake_run(cmd, **kwargs):
            seen_env["env"] = kwargs.get("env")
            return make_result(returncode=0, stdout="")

        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect"),
            contexts=[("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={self.U_DESKTOP: (0, "")},
        )
        ok, _ = actions.check_docker()
        assert ok is True
        monkeypatch.setattr(subprocess, "run", fake_run)
        actions._run(["docker", "ps"])
        assert seen_env["env"] is not None
        assert seen_env["env"]["DOCKER_HOST"] == self.U_DESKTOP

    def test_docker_contexts_parses_cli_output(self, monkeypatch) -> None:
        stdout = "default\tunix:///var/run/docker.sock\ttrue\ndesktop-linux\tunix:///home/u/.docker/desktop/docker.sock\tfalse\n"
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(returncode=0, stdout=stdout))
        assert actions._docker_contexts() == [
            ("default", "unix:///var/run/docker.sock", True),
            ("desktop-linux", "unix:///home/u/.docker/desktop/docker.sock", False),
        ]

    def test_docker_contexts_degrades_on_old_cli(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(returncode=1, stderr="unknown command"))
        assert actions._docker_contexts() == []


# --- low-level docker helpers (the layer the cleanup/uninstall flows mock) ---


class TestDockerOp:
    def test_success(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result())
        assert actions._docker_op(["docker", "rm", "x"]) == (True, "")

    def test_failure_returns_last_stderr_line(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(returncode=1, stderr="first\nlast line"))
        assert actions._docker_op(["docker", "rm", "x"]) == (False, "last line")

    def test_failure_empty_stderr(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(returncode=1))
        assert actions._docker_op(["docker", "rm", "x"]) == (False, "unknown error")

    def test_docker_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert actions._docker_op(["docker", "rm", "x"]) == (False, "docker not found")

    def test_timeout(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=60)

        monkeypatch.setattr(actions, "_run", boom)
        assert actions._docker_op(["docker", "rm", "x"]) == (False, "timed out")


class TestProjectContainers:
    def test_parses_id_name_pairs(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="abc\tapp-web\ndef\tapp-db\n"))
        pairs = actions._project_containers(config, running_only=False)
        assert pairs == [("abc", "app-web"), ("def", "app-db")]

    def test_missing_name_falls_back_to_id(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="abc\t\n"))
        assert actions._project_containers(config, running_only=False) == [("abc", "abc")]

    def test_running_only_omits_dash_a(self, config, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            return make_result()

        monkeypatch.setattr(actions, "_run", fake_run)
        actions._project_containers(config, running_only=True)
        assert "-a" not in seen["cmd"]
        actions._project_containers(config, running_only=False)
        assert "-a" in seen["cmd"]

    def test_docker_missing_returns_empty(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert actions._project_containers(config, running_only=False) == []

    def test_timeout_returns_empty(self, config, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(actions, "_run", boom)
        assert actions._project_containers(config, running_only=True) == []


class TestProjectImages:
    def test_parses_and_dedupes_by_id(self, config, monkeypatch) -> None:
        stdout = "id1\trepo/app\nid1\trepo/app-alias\nid2\trepo/db\n"
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout=stdout))
        assert actions._project_images(config) == [("id1", "repo/app"), ("id2", "repo/db")]

    def test_missing_ref_falls_back_to_id(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="id1\t\n"))
        assert actions._project_images(config) == [("id1", "id1")]

    def test_uses_image_patterns_as_reference_filters(self, config, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            return make_result()

        monkeypatch.setattr(actions, "_run", fake_run)
        actions._project_images(config)
        filters = [seen["cmd"][i + 1] for i, arg in enumerate(seen["cmd"]) if arg == "--filter"]
        assert filters, "expected at least one --filter reference=..."
        assert all(f.startswith("reference=*") for f in filters)

    def test_docker_missing_returns_empty(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert actions._project_images(config) == []


class TestDockerNames:
    def test_container_kind_uses_ps(self, config, monkeypatch) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **k):
            seen.append(cmd)
            return make_result(stdout="app-web\n")

        monkeypatch.setattr(actions, "_run", fake_run)
        names = actions._docker_names(config, "container", ("app",))
        assert names == ["app-web"]
        assert seen[0][:3] == ["docker", "ps", "-a"]

    def test_volume_kind_uses_volume_ls(self, config, monkeypatch) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **k):
            seen.append(cmd)
            return make_result(stdout="app-data\n")

        monkeypatch.setattr(actions, "_run", fake_run)
        names = actions._docker_names(config, "volume", ("app",))
        assert names == ["app-data"]
        assert seen[0][:4] == ["docker", "volume", "ls", "--format"]

    def test_dedupes_across_patterns(self, config, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="same\n"))
        assert actions._docker_names(config, "container", ("a", "b")) == ["same"]

    def test_empty_pattern_skipped(self, config, monkeypatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **k):
            calls.append(cmd)
            return make_result()

        monkeypatch.setattr(actions, "_run", fake_run)
        actions._docker_names(config, "container", ("", "app"))
        assert len(calls) == 1

    def test_error_on_one_pattern_continues(self, config, monkeypatch) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd="docker", timeout=15)
            return make_result(stdout="found\n")

        monkeypatch.setattr(actions, "_run", fake_run)
        assert actions._docker_names(config, "container", ("a", "b")) == ["found"]


class TestImageSizeBytes:
    def test_valid_size(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="123456789\n"))
        assert actions._image_size_bytes("repo/app:latest") == 123456789

    def test_nonzero_returncode(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(returncode=1, stderr="no such image"))
        assert actions._image_size_bytes("gone") == 0

    def test_non_numeric_output(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: make_result(stdout="not-a-number"))
        assert actions._image_size_bytes("weird") == 0

    def test_docker_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(actions, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert actions._image_size_bytes("x") == 0

    def test_timeout(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(actions, "_run", boom)
        assert actions._image_size_bytes("x") == 0


class TestRemoveConfigPath:
    def test_removes_file(self, tmp_path) -> None:
        target = tmp_path / "stale.json"
        target.write_text("{}")
        assert actions._remove_config_path(str(target)) == (True, "")
        assert not target.exists()

    def test_removes_directory(self, tmp_path) -> None:
        target = tmp_path / "stale-dir"
        (target / "sub").mkdir(parents=True)
        (target / "sub" / "f.txt").write_text("x")
        assert actions._remove_config_path(str(target)) == (True, "")
        assert not target.exists()

    def test_nonexistent_is_ok(self, tmp_path) -> None:
        assert actions._remove_config_path(str(tmp_path / "gone")) == (True, "")

    def test_oserror_reported(self, tmp_path, monkeypatch) -> None:
        target = tmp_path / "stale-dir"
        target.mkdir()

        def boom(*a, **k):
            raise OSError("permission denied")

        monkeypatch.setattr(shutil, "rmtree", boom)
        ok, detail = actions._remove_config_path(str(target))
        assert ok is False and "permission denied" in detail


class _FakePopen:
    """Deterministic Popen stand-in: yields prepared lines, then exits."""

    def __init__(self, lines: list[str], returncode: int = 0, hang_after: bool = False) -> None:
        self._lines = lines
        self.returncode = returncode
        self._hang_after = hang_after
        self._killed = threading.Event()
        self.stdout = self._iter_stdout()

    def _iter_stdout(self) -> Iterator[str]:
        yield from (line + "\n" for line in self._lines)
        if self._hang_after:
            # Simulate a process that stops producing output but never exits
            # until the watchdog kills it.
            self._killed.wait(timeout=5.0)

    def kill(self) -> None:
        self.returncode = -9
        self._killed.set()

    def wait(self) -> int:
        return self.returncode


class TestStreamCommand:
    def test_streams_lines_and_returns_tail(self, monkeypatch) -> None:
        fake = _FakePopen(["one", "two", "three"])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        received: list[str] = []
        code, tail = actions._stream_command(["docker", "build"], on_output=received.append, timeout=5.0)
        assert code == 0
        assert received == ["one", "two", "three"]
        assert tail == "one\ntwo\nthree"

    def test_tail_limited_to_tail_lines(self, monkeypatch) -> None:
        fake = _FakePopen([f"line{i}" for i in range(20)])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        _, tail = actions._stream_command(["docker", "build"], timeout=5.0, tail_lines=2)
        assert tail == "line18\nline19"

    def test_keep_bounds_memory(self, monkeypatch) -> None:
        fake = _FakePopen([f"line{i}" for i in range(50)])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        _, tail = actions._stream_command(["docker", "build"], timeout=5.0, tail_lines=15, keep=10)
        # Only the last `keep` lines survive; the tail comes from those.
        assert tail.splitlines() == [f"line{i}" for i in range(40, 50)]

    def test_nonzero_returncode_passed_through(self, monkeypatch) -> None:
        fake = _FakePopen(["ERROR: build failed"], returncode=17)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        code, tail = actions._stream_command(["docker", "build"], timeout=5.0)
        assert code == 17
        assert "build failed" in tail

    def test_broken_output_callback_never_breaks_the_run(self, monkeypatch) -> None:
        fake = _FakePopen(["a", "b"])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

        def bad_callback(line: str) -> None:
            raise RuntimeError("UI died")

        code, tail = actions._stream_command(["docker", "build"], on_output=bad_callback, timeout=5.0)
        assert code == 0 and tail == "a\nb"

    def test_watchdog_timeout_raises(self, monkeypatch) -> None:
        fake = _FakePopen(["only line"], hang_after=True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        with pytest.raises(subprocess.TimeoutExpired):
            actions._stream_command(["docker", "build"], timeout=0.05)


class TestStartDockerDesktop:
    def test_windows_configured_path(self, config, monkeypatch) -> None:
        started: list[list[str]] = []
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: started.append(cmd))
        config.docker_desktop_path = r"C:\Custom\Docker Desktop.exe"
        ok, msg = actions.start_docker_desktop(config)
        assert ok is True and "starting" in msg
        assert started == [[r"C:\Custom\Docker Desktop.exe"]]

    def test_windows_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        ok, msg = actions.start_docker_desktop(config)
        assert ok is False and "not found" in msg

    def test_macos_opens_app(self, config, monkeypatch) -> None:
        started: list[list[str]] = []
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: started.append(cmd))
        ok, _ = actions.start_docker_desktop(config)
        assert ok is True
        assert started == [["open", "/Applications/Docker.app"]]

    def test_macos_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        assert actions.start_docker_desktop(config)[0] is False

    def test_popen_oserror_is_suppressed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(os.path, "exists", lambda p: True)

        def boom(*a, **k):
            raise OSError("blocked")

        monkeypatch.setattr(subprocess, "Popen", boom)
        ok, msg = actions.start_docker_desktop(config)
        assert ok is False and "not found" in msg
