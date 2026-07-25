"""Tests for :mod:`docker_app_launcher.docker.detection`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import command_runner as docker_cli
from docker_app_launcher.docker import detection as detection
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


class TestCheckDocker:
    def test_running(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda *a, **k: make_result(stdout="info"))
        ok, msg = detection.check_docker()
        assert ok is True and "running" in msg

    def test_not_installed(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok, msg = detection.check_docker()
        assert ok is False and "not installed" in msg

    def test_daemon_stopped(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda *a, **k: make_result(returncode=1, stderr="cannot connect"))
        ok, msg = detection.check_docker()
        assert ok is False and "not started" in msg

    def test_timeout(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=10)

        monkeypatch.setattr(detection, "_run", boom)
        ok, msg = detection.check_docker()
        assert ok is False and "not responding" in msg

    def test_docker_installed_true(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda *a, **k: make_result(stdout="Docker version 27"))
        ok, msg = detection.docker_installed()
        assert ok is True and "27" in msg

    def test_docker_installed_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok, _ = detection.docker_installed()
        assert ok is False


class TestCheckDockerDetailed:
    def _patch(self, monkeypatch, system, which, info) -> None:
        monkeypatch.setattr("platform.system", lambda: system)
        monkeypatch.setattr("shutil.which", lambda _x: which)
        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: info)
        monkeypatch.setattr(detection, "_docker_contexts", lambda: [])

    def test_linux_not_installed(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", None, (127, ""))
        r = detection.check_docker_detailed(config)
        assert r["installed"] is False and "apt install" in r["command"] and r["platform"] == "Linux"

    def test_linux_daemon_off_offers_start(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", "/usr/bin/docker", (1, "Cannot connect to the Docker daemon"))
        r = detection.check_docker_detailed(config)
        assert r["installed"] and not r["running"] and r["can_start"] and "systemctl start docker" in r["command"]

    def test_linux_permission_denied(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", "/usr/bin/docker", (1, "permission denied while trying to connect"))
        r = detection.check_docker_detailed(config)
        assert "usermod -aG docker" in r["command"] and not r["running"]

    def test_linux_running(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Linux", "/usr/bin/docker", (0, ""))
        assert detection.check_docker_detailed(config)["running"] is True

    def test_windows_desktop_installed_not_in_path(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Windows", None, (127, ""))
        monkeypatch.setattr("os.path.exists", lambda _p: True)
        r = detection.check_docker_detailed(config)
        assert r["installed"] and r["can_start"]

    def test_windows_not_installed(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Windows", None, (127, ""))
        monkeypatch.setattr("os.path.exists", lambda _p: False)
        assert detection.check_docker_detailed(config)["installed"] is False

    def test_darwin_app_present_not_running(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Darwin", "/usr/local/bin/docker", (1, "Cannot connect"))
        r = detection.check_docker_detailed(config)
        assert r["installed"] and r["can_start"]

    def test_never_raises_on_unknown_platform(self, config, monkeypatch) -> None:
        self._patch(monkeypatch, "Plan9", None, (127, ""))
        monkeypatch.setattr("os.path.exists", lambda _p: False)
        assert detection.check_docker_detailed(config)["installed"] is False

    def test_install_url_override(self, config, monkeypatch) -> None:
        config.docker_install_url = "https://corp/docker"
        self._patch(monkeypatch, "Linux", None, (127, ""))
        assert detection.check_docker_detailed(config)["install_url"] == "https://corp/docker"

    def test_start_docker_daemon_success(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda cmd, **k: make_result(returncode=0))
        assert detection.start_docker_daemon()[0] is True

    def test_start_docker_desktop_not_found(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setattr("os.path.exists", lambda _p: False)
        assert detection.start_docker_desktop(config)[0] is False


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

        monkeypatch.setattr(detection, "_docker_info_rc", info_rc)
        monkeypatch.setattr(detection, "_docker_contexts", lambda: contexts)

    def test_falls_back_to_other_context_and_connects(self, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"),
            contexts=[("default", self.U_DEFAULT, True), ("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={self.U_DESKTOP: (0, "")},
        )
        ok, msg = detection.check_docker()
        assert ok is True
        assert "desktop-linux" in msg
        assert docker_cli.docker_host_override() == self.U_DESKTOP

    def test_detailed_reports_fallback_context(self, config, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect"),
            contexts=[("default", self.U_DEFAULT, True), ("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={self.U_DESKTOP: (0, "")},
        )
        r = detection.check_docker_detailed(config)
        assert r["running"] is True
        assert "desktop-linux" in r["detail"]

    def test_detail_names_context_endpoint_and_docker_error(self, config, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"),
            contexts=[("default", self.U_DEFAULT, True)],
        )
        r = detection.check_docker_detailed(config)
        assert r["running"] is False and r["can_start"] is True
        assert "default" in r["detail"]
        assert self.U_DEFAULT in r["detail"]
        assert "Cannot connect to the Docker daemon" in r["detail"]

    def test_permission_denied_is_not_swept(self, config, monkeypatch) -> None:
        def sweep_must_not_be_called(*a, **k):
            raise AssertionError("permission failures must not trigger the context sweep")

        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(
            detection, "_docker_info_rc", lambda extra_env=None: (1, "permission denied while connecting")
        )
        # Active endpoint reads as the root socket (empty context list -> default),
        # so usermod is still offered, but the SWEEP must not run (#57 keeps #27).
        monkeypatch.setattr(detection, "_docker_contexts", lambda: [])
        monkeypatch.setattr(detection, "_sweep_other_contexts", sweep_must_not_be_called)
        r = detection.check_docker_detailed(config)
        assert "usermod -aG docker" in r["command"] and not r["running"]

    def test_all_contexts_dead_stays_not_running(self, monkeypatch) -> None:
        self._patch(
            monkeypatch,
            active_info=(1, "Cannot connect"),
            contexts=[("default", self.U_DEFAULT, True), ("desktop-linux", self.U_DESKTOP, False)],
            per_endpoint={},
        )
        ok, _ = detection.check_docker()
        assert ok is False
        assert docker_cli.docker_host_override() is None

    def test_active_context_ok_needs_no_sweep(self, monkeypatch) -> None:
        def contexts_must_not_be_called():
            raise AssertionError("a healthy active context must not trigger the sweep")

        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: (0, ""))
        monkeypatch.setattr(detection, "_docker_contexts", contexts_must_not_be_called)
        ok, msg = detection.check_docker()
        assert ok is True and msg == "Docker is running."
        assert docker_cli.docker_host_override() is None

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
        ok, _ = detection.check_docker()
        assert ok is True
        monkeypatch.setattr(subprocess, "run", fake_run)
        docker_cli._run(["docker", "ps"])
        assert seen_env["env"] is not None
        assert seen_env["env"]["DOCKER_HOST"] == self.U_DESKTOP

    def test_docker_contexts_parses_cli_output(self, monkeypatch) -> None:
        stdout = "default\tunix:///var/run/docker.sock\ttrue\ndesktop-linux\tunix:///home/u/.docker/desktop/docker.sock\tfalse\n"
        monkeypatch.setattr(detection, "_run", lambda *a, **k: make_result(returncode=0, stdout=stdout))
        assert detection._docker_contexts() == [
            ("default", "unix:///var/run/docker.sock", True),
            ("desktop-linux", "unix:///home/u/.docker/desktop/docker.sock", False),
        ]

    def test_docker_contexts_degrades_on_old_cli(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_run", lambda *a, **k: make_result(returncode=1, stderr="unknown command"))
        assert detection._docker_contexts() == []


class TestStartDockerDesktop:
    def test_windows_configured_path(self, config, monkeypatch) -> None:
        started: list[list[str]] = []
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: started.append(cmd))
        config.docker_desktop_path = r"C:\Custom\Docker Desktop.exe"
        ok, msg = detection.start_docker_desktop(config)
        assert ok is True and "starting" in msg
        assert started == [[r"C:\Custom\Docker Desktop.exe"]]

    def test_windows_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        ok, msg = detection.start_docker_desktop(config)
        assert ok is False and "not found" in msg

    def test_macos_opens_app(self, config, monkeypatch) -> None:
        started: list[list[str]] = []
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: started.append(cmd))
        ok, _ = detection.start_docker_desktop(config)
        assert ok is True
        assert started == [["open", "/Applications/Docker.app"]]

    def test_macos_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        assert detection.start_docker_desktop(config)[0] is False

    def test_popen_oserror_is_suppressed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(os.path, "exists", lambda p: True)

        def boom(*a, **k):
            raise OSError("blocked")

        monkeypatch.setattr(subprocess, "Popen", boom)
        ok, msg = detection.start_docker_desktop(config)
        assert ok is False and "not found" in msg


class TestCheckDockerPermission:
    """CLI path: permission denied must NOT read as 'Docker is not started'."""

    def test_permission_message_names_group_usermod_and_relogin(self, monkeypatch) -> None:
        monkeypatch.setattr(
            detection,
            "_docker_info_rc",
            lambda extra_env=None: (1, "permission denied while trying to connect to the docker API"),
        )
        ok, msg = detection.check_docker()
        assert ok is False
        assert "not started" not in msg
        assert "docker" in msg and "usermod -aG docker" in msg
        assert "log out" in msg.lower()  # the mandatory re-login hint

    def test_permission_never_triggers_context_sweep(self, monkeypatch) -> None:
        def sweep_must_not_be_called(*a, **k):
            raise AssertionError("permission failures must not trigger the context sweep")

        monkeypatch.setattr(
            detection, "_docker_info_rc", lambda extra_env=None: (1, "permission denied while connecting")
        )
        # The endpoint kind IS read (a single cheap `docker context ls`, #57),
        # but the expensive multi-context SWEEP must still never run on permission.
        monkeypatch.setattr(detection, "_docker_contexts", lambda: [])
        monkeypatch.setattr(detection, "_sweep_other_contexts", sweep_must_not_be_called)
        ok, _ = detection.check_docker()
        assert ok is False


class TestDetailedPermissionMessage:
    """GUI path: the localized message must carry usermod AND the re-login hint."""

    def _permission_result(self, config, monkeypatch) -> dict[str, Any]:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(
            detection,
            "_docker_info_rc",
            lambda extra_env=None: (1, "permission denied while trying to connect to the docker API"),
        )
        return detection.check_docker_detailed(config)

    def test_detail_contains_usermod_and_relogin(self, config, monkeypatch) -> None:
        r = self._permission_result(config, monkeypatch)
        assert "usermod -aG docker" in r["detail"]
        assert "log out" in r["detail"].lower()
        assert "newgrp docker" in r["detail"]

    def test_no_false_systemctl_suggestion(self, config, monkeypatch) -> None:
        r = self._permission_result(config, monkeypatch)
        assert "systemctl start" not in r["command"]
        assert r["can_start"] is False

    def test_linux_offers_self_repair(self, config, monkeypatch) -> None:
        r = self._permission_result(config, monkeypatch)
        assert r["can_fix_permission"] is True

    def test_windows_has_no_self_repair(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setattr("shutil.which", lambda _x: "C:/docker.exe")
        monkeypatch.setattr("os.path.exists", lambda p: True)
        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: (1, "permission denied"))
        monkeypatch.setattr(detection, "_sweep_other_contexts", lambda *a, **k: None)
        r = detection.check_docker_detailed(config)
        assert r.get("can_fix_permission", False) is False


class TestAddUserToDockerGroup:
    """Self-repair (Linux, pkexec): confirmed, verified, honest about re-login."""

    def _patch_linux(self, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr("getpass.getuser", lambda: "testuser")

    def test_success_verifies_and_demands_relogin(self, config, monkeypatch) -> None:
        self._patch_linux(monkeypatch)
        # The G1 guard reads the active endpoint first; pin it to the root socket
        # so the self-repair proceeds without an extra context-ls shell-out.
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", "unix:///var/run/docker.sock"))
        calls: list[list[str]] = []

        def fake_run(cmd, **k):
            calls.append(cmd)
            if cmd[0] == "pkexec":
                return make_result()
            return make_result(stdout="docker:x:991:otheruser,testuser\n")

        monkeypatch.setattr(detection, "_run", fake_run)
        ok, msg = detection.add_user_to_docker_group(config)
        assert ok is True
        assert calls[0][:2] == ["pkexec", "usermod"]
        assert calls[1][:3] == ["getent", "group", "docker"]
        low = msg.lower()
        assert ("log out" in low or "abmelden" in low) or "melde" in low
        assert "ready" not in low  # never claim docker is usable already

    def test_pkexec_dismissed_is_a_clean_cancel(self, config, monkeypatch) -> None:
        self._patch_linux(monkeypatch)
        monkeypatch.setattr(detection, "_run", lambda cmd, **k: make_result(returncode=126))
        ok, msg = detection.add_user_to_docker_group(config)
        assert ok is False
        assert "usermod -aG docker" in msg  # fallback to the manual instruction

    def test_pkexec_failure_reports_error(self, config, monkeypatch) -> None:
        self._patch_linux(monkeypatch)
        monkeypatch.setattr(
            detection,
            "_run",
            lambda cmd, **k: make_result(returncode=1, stderr="usermod: group 'docker' does not exist"),
        )
        ok, msg = detection.add_user_to_docker_group(config)
        assert ok is False and "does not exist" in msg

    def test_verification_failure_is_not_success(self, config, monkeypatch) -> None:
        self._patch_linux(monkeypatch)

        def fake_run(cmd, **k):
            if cmd[0] == "pkexec":
                return make_result()
            return make_result(stdout="docker:x:991:someoneelse\n")

        monkeypatch.setattr(detection, "_run", fake_run)
        ok, _ = detection.add_user_to_docker_group(config)
        assert ok is False

    def test_non_linux_refuses(self, config, monkeypatch) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        ok, _ = detection.add_user_to_docker_group(config)
        assert ok is False

    def test_pkexec_missing_reports_error(self, config, monkeypatch) -> None:
        self._patch_linux(monkeypatch)
        monkeypatch.setattr(detection, "_run", lambda cmd, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok, msg = detection.add_user_to_docker_group(config)
        assert ok is False and "usermod -aG docker" in msg


class TestWaitForDocker:
    """After starting Docker Desktop/daemon: poll instead of instantly failing (#28)."""

    def _no_sleep(self, monkeypatch) -> None:
        monkeypatch.setattr(time, "sleep", lambda s: None)

    def test_immediately_running(self, config, monkeypatch) -> None:
        self._no_sleep(monkeypatch)
        monkeypatch.setattr(detection, "check_docker", lambda: (True, "Docker is running."))
        ok, msg = detection.wait_for_docker(config, timeout=10.0)
        assert ok is True and msg

    def test_becomes_ready_after_a_few_polls(self, config, monkeypatch) -> None:
        self._no_sleep(monkeypatch)
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            return (attempts["n"] >= 3, "Docker is running." if attempts["n"] >= 3 else "not yet")

        monkeypatch.setattr(detection, "check_docker", flaky)
        ok, _ = detection.wait_for_docker(config, timeout=60.0, interval=0.1)
        assert ok is True
        assert attempts["n"] == 3

    def test_timeout_reports_honestly(self, config, monkeypatch) -> None:
        self._no_sleep(monkeypatch)
        ticks = iter(range(0, 1000, 10))  # monotonic jumps 10s per call -> timeout fast
        monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(detection, "check_docker", lambda: (False, "still down"))
        ok, msg = detection.wait_for_docker(config, timeout=30.0)
        assert ok is False
        assert msg  # never empty - the user must see WHY

    def test_progress_callback_receives_waiting_label(self, config, monkeypatch) -> None:
        self._no_sleep(monkeypatch)
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            return (attempts["n"] >= 2, "ok" if attempts["n"] >= 2 else "not yet")

        monkeypatch.setattr(detection, "check_docker", flaky)
        labels: list[str] = []
        ok, _ = detection.wait_for_docker(config, timeout=60.0, on_progress=lambda pct, label: labels.append(label))
        assert ok is True
        assert labels, "waiting progress must be reported"


class TestDetectionStepLogging:
    """The context sweep must report each probed endpoint (#30)."""

    def test_detailed_reports_each_swept_context(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(
            detection, "_docker_info_rc", lambda extra_env=None: (1, "Cannot connect to the Docker daemon")
        )
        monkeypatch.setattr(
            detection,
            "_docker_contexts",
            lambda: [
                ("default", "unix:///dead.sock", True),
                ("desktop-linux", "unix:///desk.sock", False),
                ("rootless", "unix:///root.sock", False),
            ],
        )
        steps: list[str] = []
        result = detection.check_docker_detailed(config, on_step=steps.append)
        assert result["running"] is False
        assert sum("desktop-linux" in s for s in steps) == 1
        assert sum("rootless" in s for s in steps) == 1
        assert not any("'default'" in s for s in steps)  # active context is not re-probed

    def test_detailed_without_callback_stays_silent_and_working(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: (0, ""))
        assert detection.check_docker_detailed(config)["running"] is True


class TestReloginTransitionSimulation:
    """Simulates the group-membership transition a real re-login would cause.

    HONEST LIMIT: this proves the DETECTION LOGIC reacts correctly to the
    before/after states - it does NOT prove that a real logout/login on a
    real system produces the after state (kernel session mechanics are not
    simulated here).
    """

    def _docker_env(self, monkeypatch, session: dict[str, bool]) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")

        def info_rc(extra_env=None):
            if session["relogged_in"]:
                return (0, "")
            return (1, "permission denied while trying to connect to the docker API")

        monkeypatch.setattr(detection, "_docker_info_rc", info_rc)

    def test_before_permission_message_after_running(self, config, monkeypatch) -> None:
        session = {"relogged_in": False}
        self._docker_env(monkeypatch, session)

        before = detection.check_docker_detailed(config)
        assert before["running"] is False
        assert "usermod -aG docker" in before["detail"]
        assert before["can_fix_permission"] is True
        ok, msg = detection.check_docker()
        assert ok is False and "log out" in msg.lower()

        session["relogged_in"] = True  # what a real re-login would change

        after = detection.check_docker_detailed(config)
        assert after["running"] is True
        ok, msg = detection.check_docker()
        assert ok is True and "running" in msg.lower()

    def test_usermod_without_relogin_keeps_the_message(self, config, monkeypatch) -> None:
        # usermod succeeded but the session still has the old groups: the
        # launcher must keep showing the permission message, never a false
        # "fixed now".
        session = {"relogged_in": False}
        self._docker_env(monkeypatch, session)
        before = detection.check_docker_detailed(config)
        again = detection.check_docker_detailed(config)  # after usermod, no re-login
        assert before["detail"] == again["detail"]
        assert again["running"] is False


class TestErrnoBasedPermissionClassification:
    """Device finding (#27 reopened): classification must come from the actual
    socket signal, never from an unguaranteed CLI message string. These tests
    build a REAL socket with the target errno - no mock of the probe itself."""

    def _generic_cli(self, monkeypatch) -> None:
        # The device class: CLI reports the generic connect message even
        # though the underlying failure is EACCES.
        monkeypatch.setattr(
            detection,
            "_docker_info_rc",
            lambda extra_env=None: (
                1,
                "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
            ),
        )
        monkeypatch.setattr(detection, "_docker_contexts", lambda: [])

    def _denied_socket(self, tmp_path) -> str:
        import socket as socket_module

        path = str(tmp_path / "s.sock")
        server = socket_module.socket(socket_module.AF_UNIX)
        server.bind(path)
        server.listen(1)
        os.chmod(path, 0o000)
        return f"unix://{path}"

    def test_eacces_socket_classifies_as_permission_despite_generic_cli_text(
        self, config, tmp_path, monkeypatch
    ) -> None:
        endpoint = self._denied_socket(tmp_path)
        self._generic_cli(monkeypatch)
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", endpoint))
        ok, msg = detection.check_docker()
        assert ok is False
        assert "usermod -aG docker" in msg, f"EACCES misclassified: {msg!r}"
        assert "not started" not in msg

    def test_detailed_eacces_offers_the_permission_fix(self, config, tmp_path, monkeypatch) -> None:
        endpoint = self._denied_socket(tmp_path)
        self._generic_cli(monkeypatch)
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", endpoint))
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        info = detection.check_docker_detailed(config)
        assert info["can_fix_permission"] is True, f"got: {info['detail']!r}"
        assert info["can_start"] is False
        assert "usermod -aG docker" in info["detail"]

    def test_missing_socket_stays_daemon_down_with_start_offer(self, config, tmp_path, monkeypatch) -> None:
        endpoint = f"unix://{tmp_path}/never-created.sock"
        self._generic_cli(monkeypatch)
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", endpoint))
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        info = detection.check_docker_detailed(config)
        assert info["can_fix_permission"] is False
        assert info["can_start"] is True
        ok, msg = detection.check_docker()
        assert ok is False and "not started" in msg


class TestNativeApiDetection:
    """#44: the docker-py ping is authoritative for ok/permission; everything
    else falls back to the CLI probe."""

    def test_api_ok_short_circuits_cli(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("ok", ""))

        def cli_must_not_run(*a, **k):
            raise AssertionError("CLI probe must not run when the API answered")

        monkeypatch.setattr(detection, "_docker_info_rc", cli_must_not_run)
        ok, msg = detection.check_docker()
        assert ok is True and "running" in msg

    def test_api_permission_short_circuits_cli(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("permission", "EACCES"))
        ok, msg = detection.check_docker()
        assert ok is False
        assert "not started" not in msg
        assert "usermod -aG docker" in msg

    def test_api_down_still_detects_missing_cli(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("down", "refused"))
        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: (127, "docker not found"))
        ok, msg = detection.check_docker()
        assert ok is False and "not installed" in msg

    def test_api_down_sweeps_contexts(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("down", "refused"))
        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: (1, "cannot connect"))
        monkeypatch.setattr(detection, "_sweep_other_contexts", lambda *a, **k: ("desktop-linux", "unix:///d.sock"))
        ok, msg = detection.check_docker()
        assert ok is True and "desktop-linux" in msg

    def test_api_unavailable_uses_cli_verdict(self, monkeypatch) -> None:
        # The conftest isolation fixture already forces "unavailable".
        monkeypatch.setattr(detection, "_docker_info_rc", lambda extra_env=None: (0, ""))
        ok, _ = detection.check_docker()
        assert ok is True

    def test_detailed_linux_permission_from_api(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("permission", "EACCES"))
        r = detection.check_docker_detailed(config)
        assert r["can_fix_permission"] is True and not r["running"]
        assert "usermod -aG docker" in r["command"]

    def test_sweep_probes_endpoints_via_api(self, monkeypatch) -> None:
        probed: list[str] = []

        def fake_ping(endpoint=None):
            probed.append(endpoint or "<env>")
            return ("ok", "") if endpoint == "unix:///d.sock" else ("down", "")

        monkeypatch.setattr(detection, "_api_ping", fake_ping)
        monkeypatch.setattr(
            detection,
            "_docker_contexts",
            lambda: [("default", "unix:///var/run/docker.sock", True), ("desktop", "unix:///d.sock", False)],
        )
        hit = detection._sweep_other_contexts()
        assert hit == ("desktop", "unix:///d.sock")
        assert probed == ["unix:///d.sock"]
        assert docker_cli.docker_host_override() == "unix:///d.sock"

    def test_probe_endpoint_cli_fallback(self, monkeypatch) -> None:
        # API unavailable (conftest default) -> the CLI probes the endpoint.
        seen_env: list[dict[str, str] | None] = []

        def fake_info_rc(extra_env=None):
            seen_env.append(extra_env)
            return (0, "")

        monkeypatch.setattr(detection, "_docker_info_rc", fake_info_rc)
        assert detection._probe_endpoint("unix:///x.sock") is True
        assert seen_env == [{"DOCKER_HOST": "unix:///x.sock"}]


class TestEndpointAwareDetection:
    """G1 (#57) + G2 (#62): the docker-group fix is only correct on the classic
    root socket, and the rootless socket must be probed."""

    _ROOTLESS = "unix:///run/user/1000/docker.sock"
    _ROOT = "unix:///var/run/docker.sock"

    @pytest.mark.parametrize(
        ("endpoint", "expected"),
        [
            ("unix:///var/run/docker.sock", True),
            ("unix:///run/docker.sock", True),
            ("unix:///run/user/1000/docker.sock", False),  # rootless
            ("unix:///home/u/.docker/desktop/docker.sock", False),  # Desktop
            ("tcp://192.168.1.5:2375", False),  # remote
            ("npipe:////./pipe/docker_engine", False),  # Windows
        ],
    )
    def test_is_local_root_socket(self, endpoint, expected, monkeypatch) -> None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert detection._is_local_root_socket(endpoint) is expected

    def test_xdg_runtime_dir_socket_is_not_root(self, monkeypatch) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/custom/run")
        assert detection._is_local_root_socket("unix:///custom/run/docker.sock") is False

    # --- G1: permission advice gated by endpoint kind ---

    def test_check_docker_permission_on_root_offers_usermod(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_daemon_status", lambda: ("permission", "denied"))
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", self._ROOT))
        ok, msg = detection.check_docker()
        assert ok is False and "usermod -aG docker" in msg

    def test_check_docker_permission_on_rootless_does_not_offer_usermod(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_daemon_status", lambda: ("permission", "denied"))
        monkeypatch.setattr(detection, "_active_context", lambda: ("rootless", self._ROOTLESS))
        ok, msg = detection.check_docker()
        assert ok is False
        assert "usermod -aG docker" not in msg
        assert "DOCKER_HOST" in msg  # rootless-appropriate guidance

    def test_detailed_permission_on_rootless_has_no_group_fix(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(detection, "_daemon_status", lambda: ("permission", "denied"))
        monkeypatch.setattr(detection, "_active_context", lambda: ("rootless", self._ROOTLESS))
        r = detection.check_docker_detailed(config)
        assert r["can_fix_permission"] is False
        assert "usermod" not in r["command"]

    def test_detailed_permission_on_root_keeps_group_fix(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/docker")
        monkeypatch.setattr(detection, "_daemon_status", lambda: ("permission", "denied"))
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", self._ROOT))
        r = detection.check_docker_detailed(config)
        assert r["can_fix_permission"] is True and "usermod -aG docker" in r["command"]

    def test_add_user_to_group_refuses_non_root_socket(self, config, monkeypatch) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr(detection, "_active_context", lambda: ("rootless", self._ROOTLESS))
        ran = {"pkexec": False}

        def must_not_run(*a, **k):
            ran["pkexec"] = True
            return make_result()

        monkeypatch.setattr(detection, "_run", must_not_run)
        ok, _msg = detection.add_user_to_docker_group(config)
        assert ok is False and ran["pkexec"] is False

    # --- G2: rootless socket probed as a fallback ---

    def test_rootless_socket_endpoint_from_xdg(self, monkeypatch) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        monkeypatch.setattr("os.path.exists", lambda p: p == "/run/user/1000/docker.sock")
        assert detection._rootless_socket_endpoint() == self._ROOTLESS

    def test_rootless_socket_endpoint_none_without_xdg(self, monkeypatch) -> None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert detection._rootless_socket_endpoint() is None

    def test_rootless_socket_probed_when_active_is_down(self, monkeypatch) -> None:
        monkeypatch.setattr(detection, "_daemon_status", lambda: ("down", "cannot connect"))
        monkeypatch.setattr(detection, "_docker_contexts", lambda: [])
        monkeypatch.setattr(detection, "_active_context", lambda: ("default", self._ROOT))
        monkeypatch.setattr(detection, "_rootless_socket_endpoint", lambda: self._ROOTLESS)
        monkeypatch.setattr(detection, "_probe_endpoint", lambda ep: ep == self._ROOTLESS)
        ok, msg = detection.check_docker()
        assert ok is True and "rootless" in msg
        assert docker_cli.docker_host_override() == self._ROOTLESS
