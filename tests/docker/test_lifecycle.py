"""Tests for :mod:`docker_app_launcher.docker.lifecycle`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import socket
import threading
import urllib.request
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import inventory
from docker_app_launcher import launcher_settings as settings
from docker_app_launcher.docker import lifecycle as lifecycle
from tests.conftest import make_result


def _bind_free_port() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _make_repo(config: LauncherConfig) -> None:
    config.compose_path.parent.mkdir(parents=True, exist_ok=True)
    config.compose_path.write_text("services: {}\n")


@pytest.fixture
def iconfig(config):
    """A config that declares two internal ports (backend + nginx)."""
    config.internal_ports = {"backend": 8000, "nginx": 80}
    config.env_internal_port_keys = {"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"}
    config.show_advanced_ports = True
    return config


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


class TestGetState:
    def test_no_docker(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        assert lifecycle.get_state(config) == "no_docker"

    def test_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_project_container_ids", lambda c, *, running_only: [])
        assert lifecycle.get_state(config) == "not_installed"

    def test_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_project_container_ids", lambda c, *, running_only: ["c1"])
        assert lifecycle.get_state(config) == "running"

    def test_stopped(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(
            lifecycle, "_project_container_ids", lambda c, *, running_only: [] if running_only else ["c1"]
        )
        assert lifecycle.get_state(config) == "stopped"

    def test_uses_config_filters(self, config, monkeypatch) -> None:
        seen = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            return make_result(stdout="")

        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(inventory, "_run", fake_run)
        lifecycle.get_state(config)
        assert "name=test-app" in seen["cmd"]


class TestInstall:
    def test_success(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = lifecycle.install(config)
        assert ok is True and "ready" in msg
        assert config.manifest_path.is_file()

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        ok, msg = lifecycle.install(config)
        assert ok is False and "not available" in msg

    def test_already_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        ok, msg = lifecycle.install(config)
        assert ok is True and "already installed" in msg

    def test_missing_compose(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        ok, msg = lifecycle.install(config)
        assert ok is False and "Compose" in msg

    def test_port_occupied(self, config, monkeypatch) -> None:
        _make_repo(config)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (False, "busy"))
        ok, msg = lifecycle.install(config)
        assert ok is False and "occupied" in msg

    def test_build_failure(self, config, monkeypatch) -> None:
        _make_repo(config)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (1, "boom"))
        ok, msg = lifecycle.install(config)
        assert ok is False and "boom" in msg

    def test_unhealthy(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (False, "no route"))
        ok, msg = lifecycle.install(config)
        assert ok is False and "not reachable" in msg

    def test_on_step_called(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        steps: list[str] = []
        lifecycle.install(config, on_step=steps.append)
        assert any("Building" in s for s in steps)

    def test_on_progress_reaches_0_and_100_with_indeterminate_health(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        pcts: list[int | None] = []
        lifecycle.install(config, on_progress=lambda pct, label: pcts.append(pct))
        assert pcts[0] == 0
        assert pcts[-1] == 100
        assert None in pcts  # indeterminate during the health check


class TestStart:
    def test_success(self, config, monkeypatch) -> None:
        states = iter(["stopped", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = lifecycle.start(config)
        assert ok is True and "started" in msg

    def test_already_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        ok, msg = lifecycle.start(config)
        assert ok is True and "already running" in msg

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        ok, _ = lifecycle.start(config)
        assert ok is False

    def test_compose_failure(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (1, "fail"))
        ok, msg = lifecycle.start(config)
        assert ok is False and "fail" in msg

    def test_no_container_after(self, config, monkeypatch) -> None:
        states = iter(["stopped", "stopped"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        ok, _ = lifecycle.start(config)
        assert ok is False


class TestStop:
    def test_success(self, config, monkeypatch) -> None:
        states = iter(["running", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(
            lifecycle, "_project_container_ids", lambda c, *, running_only: [] if running_only else ["c"]
        )
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result())
        ok, msg = lifecycle.stop(config)
        assert ok is True and "stopped" in msg

    def test_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        ok, _ = lifecycle.stop(config)
        assert ok is False

    def test_already_stopped(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        ok, msg = lifecycle.stop(config)
        assert ok is True and "already" in msg

    def test_verify_still_running(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        monkeypatch.setattr(lifecycle, "_project_container_ids", lambda c, *, running_only: ["c"])
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result())
        ok, _ = lifecycle.stop(config)
        assert ok is False


class TestChangePort:
    def test_invalid_port_rejected(self, config) -> None:
        ok, _ = lifecycle.change_port(config, 1)
        assert ok is False

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        ok, msg = lifecycle.change_port(config, 9000)
        assert ok is False and "not available" in msg

    def test_not_running_only_persists(self, config, monkeypatch) -> None:
        # Stack stopped -> persist the port (a later start picks it up), do NOT
        # touch Compose. resolve_port reflects the new value afterwards.
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        ok, _ = lifecycle.change_port(config, 9000)
        assert ok is True
        assert settings.resolve_port(config) == 9000

    def test_running_stop_restart_healthcheck(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["running", "running"])  # initial probe, then post-restart
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = lifecycle.change_port(config, 9000)
        assert ok is True and "9000" in msg
        assert settings.resolve_port(config) == 9000

    def test_restart_uses_no_build(self, config, monkeypatch) -> None:
        # A public-port change must recreate WITHOUT --build (seconds, not the
        # minutes a rebuild costs). The internal-port rebuild path is separate.
        captured: dict[str, tuple[str, ...]] = {}
        states = iter(["running", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))

        def fake_stream(c, *args, **kwargs):
            captured["args"] = args
            return (0, "")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        lifecycle.change_port(config, 9000)
        assert captured["args"] == ("up", "-d")
        assert "--build" not in captured["args"]

    def test_stop_failure_aborts(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        monkeypatch.setattr(lifecycle, "stop", lambda c: (False, "cannot stop"))
        ok, msg = lifecycle.change_port(config, 9000)
        assert ok is False and "cannot stop" in msg

    def test_unhealthy_after_restart(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["running", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (False, "no route"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = lifecycle.change_port(config, 9000)
        assert ok is False and "not reachable" in msg

    def test_health_check_targets_new_port(self, config, monkeypatch) -> None:
        _make_repo(config)
        seen: dict[str, int] = {}
        states = iter(["running", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))

        def fake_health(c, port=None):
            seen["port"] = port
            return (True, "ok")

        monkeypatch.setattr(lifecycle, "health_check", fake_health)
        lifecycle.change_port(config, 9000)
        assert seen["port"] == 9000


class TestUninstall:
    def test_success_verbose(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_project_containers", lambda c, *, running_only: [("c1", "test-app")])
        monkeypatch.setattr(lifecycle, "_project_container_ids", lambda c, *, running_only: [])
        monkeypatch.setattr(lifecycle, "_docker_op", lambda cmd, **k: (True, ""))
        monkeypatch.setattr(lifecycle, "_project_images", lambda c: [])
        steps: list[str] = []
        ok, msg = lifecycle.uninstall(config, on_step=steps.append)
        assert ok is True and "preserved" in msg
        assert any("test-app" in s and "✓" in s for s in steps)

    def test_nothing_to_uninstall(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_project_containers", lambda c, *, running_only: [])
        monkeypatch.setattr(lifecycle, "_project_images", lambda c: [])
        ok, msg = lifecycle.uninstall(config)
        assert ok is True and "Nothing to uninstall" in msg

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        ok, _ = lifecycle.uninstall(config)
        assert ok is False

    def test_partial_failure(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_project_containers", lambda c, *, running_only: [("c1", "test-app")])
        monkeypatch.setattr(lifecycle, "_project_container_ids", lambda c, *, running_only: ["c1"])
        monkeypatch.setattr(lifecycle, "_docker_op", lambda cmd, **k: (False, "denied"))
        ok, msg = lifecycle.uninstall(config)
        assert ok is False and "could not be removed" in msg


class TestHealth:
    def test_healthy_json(self, config, monkeypatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(200, '{"status": "ok"}'))
        assert lifecycle.is_healthy(config, 8080) is True

    def test_status_mismatch(self, config, monkeypatch) -> None:
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(200, '{"status": "bad"}')
        )
        assert lifecycle.is_healthy(config, 8080) is False

    def test_non_200(self, config, monkeypatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(503, ""))
        assert lifecycle.is_healthy(config, 8080) is False

    def test_no_key_means_200_is_enough(self, monkeypatch) -> None:
        cfg = LauncherConfig(app_name="X", health_check_key="").resolve()
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=3.0: _fake_response(200, "hi"))
        assert lifecycle.is_healthy(cfg, 8080) is True

    def test_connection_error(self, config, monkeypatch) -> None:
        def boom(url, timeout=3.0):
            raise OSError("refused")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        assert lifecycle.is_healthy(config, 8080) is False

    def test_health_check_times_out(self, config, monkeypatch) -> None:
        cfg = config
        cfg.health_check_timeout = 0
        monkeypatch.setattr(lifecycle, "_health_probe", lambda c, p: (False, "nope"))
        ok, msg = lifecycle.health_check(cfg, 8080)
        assert ok is False and "not reachable" in msg

    def test_open_browser_uses_browser_path(self, monkeypatch) -> None:
        cfg = LauncherConfig(app_name="X", browser_path="/dashboard").resolve()
        opened: list[str] = []
        monkeypatch.setattr(webbrowser, "open", opened.append)
        lifecycle.open_browser(cfg, 8080)
        assert opened == ["http://localhost:8080/dashboard"]

    def test_open_browser_never_raises(self, config, monkeypatch) -> None:
        def boom(url):
            raise OSError("no browser")

        monkeypatch.setattr(webbrowser, "open", boom)
        lifecycle.open_browser(config, 8080)  # should not raise


class TestGetAppVersion:
    """#35: the About surface must report the ACTUALLY RUNNING app version.

    Ladder: running health payload -> install manifest -> config.app_version
    -> unknown. Each step fails open to the next.
    """

    def _cfg(self, *, app_version: str = "9.0.0", health_key: str | None = None) -> LauncherConfig:
        cfg = LauncherConfig(app_name="X", app_version=app_version)
        if health_key is not None:
            cfg.app_version_health_key = health_key
        return cfg.resolve()

    @staticmethod
    def _payload(value: dict[str, str] | None):
        def fetch(config: LauncherConfig, port: int, timeout: float = 1.5) -> dict[str, str] | None:
            return value

        return fetch

    def test_running_health_payload_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg()
        monkeypatch.setattr(lifecycle, "_health_payload", self._payload({"status": "ok", "version": "2.6.0"}))
        assert lifecycle.get_app_version(cfg) == ("2.6.0", "running")

    def test_empty_health_key_disables_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg(health_key="")

        def boom(config: LauncherConfig, port: int, timeout: float = 1.5) -> dict[str, str] | None:
            raise AssertionError("health probe must be skipped")

        monkeypatch.setattr(lifecycle, "_health_payload", boom)
        monkeypatch.setattr(lifecycle, "read_manifest", lambda config: {"app_version": "1.2.3"})
        assert lifecycle.get_app_version(cfg) == ("1.2.3", "installed")

    def test_stopped_falls_back_to_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg()
        monkeypatch.setattr(lifecycle, "_health_payload", self._payload(None))
        monkeypatch.setattr(lifecycle, "read_manifest", lambda config: {"app_version": "1.2.3"})
        assert lifecycle.get_app_version(cfg) == ("1.2.3", "installed")

    def test_not_installed_falls_back_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg()
        monkeypatch.setattr(lifecycle, "_health_payload", self._payload(None))
        monkeypatch.setattr(lifecycle, "read_manifest", lambda config: None)
        assert lifecycle.get_app_version(cfg) == ("9.0.0", "expected")

    def test_nothing_known_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg(app_version="")
        monkeypatch.setattr(lifecycle, "_health_payload", self._payload(None))
        monkeypatch.setattr(lifecycle, "read_manifest", lambda config: None)
        assert lifecycle.get_app_version(cfg) == ("", "unknown")

    def test_health_payload_without_version_key_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg()
        monkeypatch.setattr(lifecycle, "_health_payload", self._payload({"status": "ok"}))
        monkeypatch.setattr(lifecycle, "read_manifest", lambda config: {"app_version": "1.2.3"})
        assert lifecycle.get_app_version(cfg) == ("1.2.3", "installed")

    def test_uninstalled_manifest_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._cfg()
        monkeypatch.setattr(lifecycle, "_health_payload", self._payload(None))
        monkeypatch.setattr(
            lifecycle, "read_manifest", lambda config: {"app_version": "0.3.0", "status": "uninstalled"}
        )
        assert lifecycle.get_app_version(cfg) == ("9.0.0", "expected")
