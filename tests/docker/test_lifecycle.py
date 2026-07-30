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

from docker_app_launcher import i18n
from docker_app_launcher import launcher_settings as settings
from docker_app_launcher.config import LauncherConfig

# Bound at import time - BEFORE the conftest isolation fixture pins _probe -
# so ladder tests can exercise the real detection against mocked _run.
from docker_app_launcher.docker import compose_runtime as _compose_runtime
from docker_app_launcher.docker import inventory
from docker_app_launcher.docker import lifecycle as lifecycle
from docker_app_launcher.docker.command_runner import BuildCancelled
from tests.conftest import make_result

_REAL_COMPOSE_PROBE = _compose_runtime._probe


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
        _make_repo(config)  # start now runs the build capability gate before (re)building (#54)
        states = iter(["stopped", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = lifecycle.start(config)
        assert ok is True and "started" in msg

    def test_compose_rebuild_forces_recreate(self, config, monkeypatch) -> None:
        # --build alone rebuilds the image but compose may restart the OLD
        # container on the OLD image (config-hash unchanged), so a code change
        # never runs. start() must pass --force-recreate on the compose rebuild
        # (measured: the #88 compose rebuild cell served stale content).
        _make_repo(config)
        states = iter(["stopped", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        captured: dict[str, tuple[str, ...]] = {}

        def fake_stream(c, *a, **k):
            captured["args"] = a
            return (0, "")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        ok, _ = lifecycle.start(config)
        assert ok is True
        assert captured["args"] == ("up", "--build", "-d", "--force-recreate")

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
        _make_repo(config)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (1, "fail"))
        ok, msg = lifecycle.start(config)
        assert ok is False and "fail" in msg

    def test_no_container_after(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["stopped", "stopped"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        ok, _ = lifecycle.start(config)
        assert ok is False


class TestBuildCapabilityGate:
    """The compose build gate fires BEFORE the build, not during it (#54).

    Device forensics: compose plugin present, buildx 0.8.2, the build started
    and failed minutes in with 'compose build requires buildx 0.17 or later'.
    The gate must surface that up front and never reach the build stream.
    """

    def _old_buildx(self, monkeypatch) -> None:
        from packaging.version import Version

        from docker_app_launcher.docker import build_readiness, tool_versions

        old = tool_versions.ToolVersions(
            engine_raw="20.10.21",
            engine=Version("20.10.21"),
            compose_raw="2.40.2",
            compose=Version("2.40.2"),
            buildx_raw="0.8.2",
            buildx=Version("0.8.2"),
        )
        monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: old)

    def test_install_blocks_old_buildx_before_build(self, config, monkeypatch) -> None:
        _make_repo(config)
        self._old_buildx(monkeypatch)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        reached_build = {"v": False}

        def fake_stream(c, *a, **k):
            reached_build["v"] = True
            return (1, "compose build requires buildx 0.17 or later")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        ok, msg = lifecycle.install(config)
        assert ok is False
        assert reached_build["v"] is False, "readiness must fail BEFORE the build stream runs"
        assert "buildx" in msg and "0.17" in msg and "0.8.2" in msg

    def test_start_blocks_old_buildx_before_build(self, config, monkeypatch) -> None:
        _make_repo(config)
        self._old_buildx(monkeypatch)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        reached_build = {"v": False}

        def fake_stream(c, *a, **k):
            reached_build["v"] = True
            return (0, "")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        ok, msg = lifecycle.start(config)
        assert ok is False and reached_build["v"] is False
        assert "buildx" in msg


class TestBuildCancellation:
    """A build cancelled from the UI (window closed mid-build, #60) is a clean
    cancellation, not a failure: the lifecycle catches :class:`BuildCancelled`
    and returns the localized ``build_cancelled`` message."""

    def test_install_reports_cancelled(self, config, monkeypatch) -> None:
        _make_repo(config)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))

        def cancel(c, *a, **k):
            raise BuildCancelled("docker compose build")

        monkeypatch.setattr(lifecycle, "_stream_compose", cancel)
        ok, msg = lifecycle.install(config, should_cancel=lambda: True)
        assert ok is False
        assert msg == i18n.t("build_cancelled", config)

    def test_start_reports_cancelled(self, config, monkeypatch) -> None:
        _make_repo(config)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")

        def cancel(c, *a, **k):
            raise BuildCancelled("docker compose up --build")

        monkeypatch.setattr(lifecycle, "_stream_compose", cancel)
        ok, msg = lifecycle.start(config, should_cancel=lambda: True)
        assert ok is False
        assert msg == i18n.t("build_cancelled", config)

    def test_should_cancel_threaded_to_the_stream(self, config, monkeypatch) -> None:
        _make_repo(config)
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        # install streams twice (build, then `up -d`); only the build carries the
        # cancel callback, so collect every call and assert the sentinel appears.
        seen: list[object] = []

        def fake_stream(c, *a, should_cancel=None, **k):
            seen.append(should_cancel)
            return (0, "")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        sentinel = object()
        lifecycle.install(config, should_cancel=sentinel)  # type: ignore[arg-type]
        assert sentinel in seen, "the cancel callback must reach the build stream"


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


class TestAppLogs:
    """P2: the container-log tail behind the "App logs" button."""

    def _up(self, monkeypatch, state="running") -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: state)

    def test_returns_log_tail(self, config, monkeypatch) -> None:
        self._up(monkeypatch)
        captured: list[list[str]] = []

        def fake_run(cmd, **k):
            captured.append(cmd)
            return make_result(stdout="web-1  | booting\nweb-1  | ready\n")

        monkeypatch.setattr(lifecycle, "_run", fake_run)
        ok, text = lifecycle.app_logs(config)
        assert ok is True
        assert "booting" in text and "ready" in text
        assert "logs" in captured[0] and "--tail" in captured[0]

    def test_tail_count_from_config(self, config, monkeypatch) -> None:
        self._up(monkeypatch)
        config.log_tail_lines = 42
        captured: list[list[str]] = []

        def fake_run(cmd, **k):
            captured.append(cmd)
            return make_result(stdout="x")

        monkeypatch.setattr(lifecycle, "_run", fake_run)
        lifecycle.app_logs(config)
        assert captured[0][captured[0].index("--tail") + 1] == "42"

    def test_explicit_lines_override(self, config, monkeypatch) -> None:
        self._up(monkeypatch)
        captured: list[list[str]] = []

        def fake_run(cmd, **k):
            captured.append(cmd)
            return make_result(stdout="x")

        monkeypatch.setattr(lifecycle, "_run", fake_run)
        lifecycle.app_logs(config, lines=7)
        assert captured[0][captured[0].index("--tail") + 1] == "7"

    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        ok, msg = lifecycle.app_logs(config)
        assert ok is False and "Docker" in msg

    def test_not_installed(self, config, monkeypatch) -> None:
        self._up(monkeypatch, state="not_installed")
        ok, _ = lifecycle.app_logs(config)
        assert ok is False

    def test_stopped_still_delivers_logs(self, config, monkeypatch) -> None:
        # A crashed container's last words matter most.
        self._up(monkeypatch, state="stopped")
        monkeypatch.setattr(lifecycle, "_run", lambda cmd, **k: make_result(stdout="web-1  | panic: boom"))
        ok, text = lifecycle.app_logs(config)
        assert ok is True and "panic: boom" in text

    def test_compose_failure_returns_first_stderr_line(self, config, monkeypatch) -> None:
        self._up(monkeypatch)
        monkeypatch.setattr(
            lifecycle, "_run", lambda cmd, **k: make_result(returncode=1, stderr="no configuration file provided\n")
        )
        ok, msg = lifecycle.app_logs(config)
        assert ok is False and "no configuration file provided" in msg

    def test_empty_output_is_a_friendly_message(self, config, monkeypatch) -> None:
        self._up(monkeypatch)
        monkeypatch.setattr(lifecycle, "_run", lambda cmd, **k: make_result(stdout=""))
        ok, msg = lifecycle.app_logs(config)
        assert ok is True and msg  # localized "no output yet", never an empty string

    def test_timeout_is_a_failed_result(self, config, monkeypatch) -> None:
        import subprocess as _subprocess

        self._up(monkeypatch)

        def boom(*a, **k):
            raise _subprocess.TimeoutExpired(cmd="docker", timeout=30)

        monkeypatch.setattr(lifecycle, "_run", boom)
        ok, msg = lifecycle.app_logs(config)
        assert ok is False and "timed out" in msg


class _FakeStreamContainer:
    def __init__(self, name: str, lines: list[bytes], seen_kwargs: list[dict[str, object]] | None = None) -> None:
        self.name = name
        self._lines = lines
        self._seen = seen_kwargs

    def logs(self, *, stream: bool, follow: bool, tail: int):
        if self._seen is not None:
            self._seen.append({"stream": stream, "follow": follow, "tail": tail})
        yield from self._lines


class _FakeStreamClient:
    def __init__(self, containers: list[_FakeStreamContainer]) -> None:
        self._containers = containers
        self.containers = self
        self.closed = False

    def list(self, *, all: bool = False, filters=None):
        return self._containers

    def close(self) -> None:
        self.closed = True


class TestStreamAppLogs:
    """#44: the live follow mode behind the future GUI tail."""

    def test_unavailable_without_dockerpy(self, config) -> None:
        # conftest pins _get_api_client to None by default.
        ok, msg = lifecycle.stream_app_logs(config, on_line=lambda _l: None)
        assert ok is False and msg

    def test_streams_lines_with_container_prefix(self, config, monkeypatch) -> None:
        client = _FakeStreamClient([_FakeStreamContainer("web-1", [b"boot\n", b"ready\n"])])
        monkeypatch.setattr(lifecycle, "_get_api_client", lambda: client)
        got: list[str] = []
        ok, _ = lifecycle.stream_app_logs(config, on_line=got.append, poll_interval=0.01)
        assert ok is True
        assert got == ["web-1 | boot", "web-1 | ready"]
        assert client.closed

    def test_multiple_containers_both_streamed(self, config, monkeypatch) -> None:
        client = _FakeStreamClient(
            [
                _FakeStreamContainer("web-1", [b"w\n"]),
                _FakeStreamContainer("db-1", [b"d\n"]),
            ]
        )
        monkeypatch.setattr(lifecycle, "_get_api_client", lambda: client)
        got: list[str] = []
        ok, _ = lifecycle.stream_app_logs(config, on_line=got.append, poll_interval=0.01)
        assert ok is True
        assert sorted(got) == ["db-1 | d", "web-1 | w"]

    def test_tail_forwarded(self, config, monkeypatch) -> None:
        seen: list[dict[str, object]] = []
        client = _FakeStreamClient([_FakeStreamContainer("web-1", [], seen_kwargs=seen)])
        monkeypatch.setattr(lifecycle, "_get_api_client", lambda: client)
        lifecycle.stream_app_logs(config, on_line=lambda _l: None, lines=33, poll_interval=0.01)
        assert seen == [{"stream": True, "follow": True, "tail": 33}]

    def test_no_containers_is_failed_result(self, config, monkeypatch) -> None:
        client = _FakeStreamClient([])
        monkeypatch.setattr(lifecycle, "_get_api_client", lambda: client)
        ok, _ = lifecycle.stream_app_logs(config, on_line=lambda _l: None)
        assert ok is False
        assert client.closed

    def test_should_stop_ends_the_follow(self, config, monkeypatch) -> None:
        import time as _time

        client = _FakeStreamClient([])

        class _EndlessContainer(_FakeStreamContainer):
            def logs(self, *, stream: bool, follow: bool, tail: int):
                # Mirrors the real socket: closing the client ends the stream.
                while not client.closed:
                    yield b"tick\n"
                    _time.sleep(0.001)

        client._containers = [_EndlessContainer("web-1", [])]
        monkeypatch.setattr(lifecycle, "_get_api_client", lambda: client)
        got: list[str] = []
        ok, _ = lifecycle.stream_app_logs(
            config, on_line=got.append, should_stop=lambda: len(got) > 3, poll_interval=0.01
        )
        assert ok is True
        assert len(got) > 3
        assert client.closed

    def test_broken_on_line_never_kills_the_stream(self, config, monkeypatch) -> None:
        client = _FakeStreamClient([_FakeStreamContainer("web-1", [b"a\n", b"b\n"])])
        monkeypatch.setattr(lifecycle, "_get_api_client", lambda: client)

        def bad(_line: str) -> None:
            raise RuntimeError("UI died")

        ok, _ = lifecycle.stream_app_logs(config, on_line=bad, poll_interval=0.01)
        assert ok is True


class TestComposeDetectionGuard:
    """#48: install must refuse BEFORE the build when no compose frontend
    exists - never surface the docker help dump as the error message."""

    _HELP_DUMP_TAIL = (
        "unknown shorthand flag: 'p' in -p\nSee 'docker --help'.\n\n"
        "Usage:  docker [OPTIONS] COMMAND\n\nA self-sufficient runtime for containers"
    )

    def _no_compose_env(self, config, monkeypatch) -> None:
        """The verified device situation: daemon fine, compose frontend absent."""
        _make_repo(config)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        # Run the REAL ladder against the VERIFIED 20.10 behaviour (rc 125 +
        # flag error, docker-compose absent). The conftest pin on _probe is
        # undone so the ladder itself runs. (RED proof before the ladder
        # existed: install invoked compose and returned the help dump.)
        from docker_app_launcher.docker import compose_runtime

        compose_runtime.reset_compose_cache()
        monkeypatch.setattr(compose_runtime, "_probe", _REAL_COMPOSE_PROBE)

        def fake_run(cmd, **k):
            if cmd[:2] == ["docker", "compose"]:
                return make_result(returncode=125, stderr="unknown shorthand flag: 'p' in -p")
            raise FileNotFoundError(f"{cmd[0]} not found")

        monkeypatch.setattr(compose_runtime, "_run", fake_run)

    def test_install_refuses_before_the_build(self, config, monkeypatch) -> None:
        self._no_compose_env(config, monkeypatch)
        streamed: list[bool] = []

        def fake_stream(c, *a, **k):
            streamed.append(True)
            return (125, self._HELP_DUMP_TAIL)

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        ok, msg = lifecycle.install(config)
        assert ok is False
        assert streamed == [], "compose must never be invoked without a detected frontend"
        assert "docker --help" not in msg and "Usage:" not in msg, f"help dump leaked into: {msg!r}"
        assert "docker-compose-plugin" in msg, "error must tell the user WHAT to install"

    def test_compose_args_use_the_detected_legacy_frontend(self, config, monkeypatch) -> None:
        from docker_app_launcher.docker import compose_runtime

        compose_runtime.reset_compose_cache()
        monkeypatch.setattr(compose_runtime, "_probe", lambda c: ("legacy", "docker-compose 1.29.2"))
        args = lifecycle._compose_args(config, "up", "-d")
        assert args[:1] == ["docker-compose"]
        assert "-p" in args and "-f" in args and args[-2:] == ["up", "-d"]
        compose_runtime.reset_compose_cache()


class TestNetworkPreflight:
    """G5 (#59): install warns the network is needed and classifies a
    network build failure distinctly."""

    def test_network_failure_markers(self) -> None:
        assert lifecycle._looks_like_network_failure("failed to resolve registry-1.docker.io")
        assert lifecycle._looks_like_network_failure("dial tcp: i/o timeout")
        assert not lifecycle._looks_like_network_failure("COPY failed: no such file")

    def test_install_classifies_network_build_failure(self, config, monkeypatch) -> None:
        _make_repo(config)
        config.min_build_disk_bytes = 0  # keep the readiness gate deterministic
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(
            lifecycle, "_stream_compose", lambda c, *a, **k: (1, "failed to resolve host registry-1.docker.io")
        )
        ok, msg = lifecycle.install(config)
        assert ok is False and "network" in msg.lower()

    def test_install_warns_internet_is_needed(self, config, monkeypatch) -> None:
        _make_repo(config)
        config.min_build_disk_bytes = 0
        states = iter(["not_installed", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "_stream_compose", lambda c, *a, **k: (0, ""))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        steps: list[str] = []
        lifecycle.install(config, on_step=steps.append)
        assert any("internet" in s.lower() for s in steps)


class TestStartEnvBeforeGate:
    def test_start_writes_env_before_the_readiness_gate(self, config, monkeypatch) -> None:
        """Symmetric with install(): the rendered-port preflight must see the
        .env the build will use, not a stale one (review finding 2026-07-28)."""
        order: list[str] = []
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        monkeypatch.setattr(lifecycle, "_write_env_ports", lambda c: order.append("env"))

        def gate(c):
            order.append("gate")
            return False, "blocked"

        monkeypatch.setattr(lifecycle, "_ensure_build_ready", gate)
        ok, _ = lifecycle.start(config)
        assert ok is False
        assert order == ["env", "gate"]
