"""Tests for :mod:`docker_app_launcher.docker.command_runner`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import command_runner as docker_cli
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


class TestDockerBuildProgress:
    def _collect(self, lines: list[str], **kw) -> list[tuple[int, str]]:
        reports: list[tuple[int, str]] = []
        parser = docker_cli.DockerBuildProgress(lambda pct, label: reports.append((pct, label)), **kw)
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


class TestDockerOp:
    def test_success(self, monkeypatch) -> None:
        monkeypatch.setattr(docker_cli, "_run", lambda *a, **k: make_result())
        assert docker_cli._docker_op(["docker", "rm", "x"]) == (True, "")

    def test_failure_returns_first_stderr_line_with_context(self, monkeypatch) -> None:
        # #49: the FIRST line carries the diagnosis (a trailing help dump
        # must never win); exit code + exact command give the forensics.
        monkeypatch.setattr(docker_cli, "_run", lambda *a, **k: make_result(returncode=1, stderr="first\nlast line"))
        assert docker_cli._docker_op(["docker", "rm", "x"]) == (False, "first (exit 1: docker rm x)")

    def test_failure_empty_stderr(self, monkeypatch) -> None:
        monkeypatch.setattr(docker_cli, "_run", lambda *a, **k: make_result(returncode=1))
        assert docker_cli._docker_op(["docker", "rm", "x"]) == (False, "unknown error (exit 1: docker rm x)")

    def test_docker_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(docker_cli, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert docker_cli._docker_op(["docker", "rm", "x"]) == (False, "docker not found")

    def test_timeout(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=60)

        monkeypatch.setattr(docker_cli, "_run", boom)
        assert docker_cli._docker_op(["docker", "rm", "x"]) == (False, "timed out")


class TestRunFailureLogging:
    """Failed subprocesses must land in the log at WARNING (P0): a captured
    stderr that only ever reached DEBUG was invisible at the default level."""

    def test_nonzero_exit_logs_warning_with_cmd_and_stderr(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: make_result(returncode=1, stderr="boom: no such image"))
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._run(["docker", "rm", "x"])
        assert any("docker rm x" in r.message and "no such image" in r.message for r in caplog.records)

    def test_success_logs_nothing_at_warning(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: make_result(stdout="ok"))
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._run(["docker", "ps"])
        assert caplog.records == []

    def test_probe_failure_stays_at_debug(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: make_result(returncode=1, stderr="cannot connect"))
        with caplog.at_level(logging.DEBUG, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._run(["docker", "info"], probe=True)
        failures = [r for r in caplog.records if "command failed" in r.message]
        assert failures and all(r.levelno == logging.DEBUG for r in failures)

    def test_timeout_logs_warning_and_raises(self, caplog, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=5)

        monkeypatch.setattr(subprocess, "run", boom)
        with (
            caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.command_runner"),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            docker_cli._run(["docker", "stop", "x"], timeout=5.0)
        assert any("timeout" in r.message and "docker stop x" in r.message for r in caplog.records)

    def test_missing_binary_logs_warning_and_raises(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        with (
            caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.command_runner"),
            pytest.raises(FileNotFoundError),
        ):
            docker_cli._run(["docker", "ps"])
        assert any("binary not found: docker" in r.message for r in caplog.records)

    def test_stream_failure_logs_warning_with_tail(self, caplog, monkeypatch) -> None:
        fake = _FakePopen(["ERROR: build failed"], returncode=17)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._stream_command(["docker", "build"], timeout=5.0)
        assert any("exit=17" in r.message and "build failed" in r.message for r in caplog.records)


class TestStreamCommand:
    def test_streams_lines_and_returns_tail(self, monkeypatch) -> None:
        fake = _FakePopen(["one", "two", "three"])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        received: list[str] = []
        code, tail = docker_cli._stream_command(["docker", "build"], on_output=received.append, timeout=5.0)
        assert code == 0
        assert received == ["one", "two", "three"]
        assert tail == "one\ntwo\nthree"

    def test_tail_limited_to_tail_lines(self, monkeypatch) -> None:
        fake = _FakePopen([f"line{i}" for i in range(20)])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        _, tail = docker_cli._stream_command(["docker", "build"], timeout=5.0, tail_lines=2)
        assert tail == "line18\nline19"

    def test_keep_bounds_memory(self, monkeypatch) -> None:
        fake = _FakePopen([f"line{i}" for i in range(50)])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        _, tail = docker_cli._stream_command(["docker", "build"], timeout=5.0, tail_lines=15, keep=10)
        # Only the last `keep` lines survive; the tail comes from those.
        assert tail.splitlines() == [f"line{i}" for i in range(40, 50)]

    def test_nonzero_returncode_passed_through(self, monkeypatch) -> None:
        fake = _FakePopen(["ERROR: build failed"], returncode=17)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        code, tail = docker_cli._stream_command(["docker", "build"], timeout=5.0)
        assert code == 17
        assert "build failed" in tail

    def test_broken_output_callback_never_breaks_the_run(self, monkeypatch) -> None:
        fake = _FakePopen(["a", "b"])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

        def bad_callback(line: str) -> None:
            raise RuntimeError("UI died")

        code, tail = docker_cli._stream_command(["docker", "build"], on_output=bad_callback, timeout=5.0)
        assert code == 0 and tail == "a\nb"

    def test_watchdog_timeout_raises(self, monkeypatch) -> None:
        fake = _FakePopen(["only line"], hang_after=True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        with pytest.raises(subprocess.TimeoutExpired):
            docker_cli._stream_command(["docker", "build"], timeout=0.05)


class TestCommandTransparency:
    """#49: every external command is announced BEFORE it runs (one
    shlex-quoted line) and its result carries the exit code - device
    forensics must never again require reconstructing the argv from source."""

    def test_command_announced_before_execution_at_info(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: make_result(stdout="ok"))
        with caplog.at_level(logging.INFO, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._run(["docker", "stop", "my app"])  # space forces quoting
        announcements = [r.message for r in caplog.records if "exec:" in r.message]
        assert announcements, "the command must be logged BEFORE execution"
        assert "'my app'" in announcements[0], "argv must be shlex-quoted"

    def test_probe_commands_stay_quiet_at_info(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: make_result(returncode=1, stderr="down"))
        with caplog.at_level(logging.INFO, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._run(["docker", "info"], probe=True)
        assert caplog.records == [], "status probes must not spam INFO"

    def test_success_result_carries_the_exit_code(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: make_result(stdout="ok"))
        with caplog.at_level(logging.INFO, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._run(["docker", "start", "x"])
        assert any("exit=0" in r.message for r in caplog.records)

    def test_docker_op_failure_names_action_code_and_first_error(self, monkeypatch) -> None:
        stderr = "unknown flag: '-p'\nUsage: docker..."
        monkeypatch.setattr(docker_cli, "_run", lambda *a, **k: make_result(returncode=125, stderr=stderr))
        ok, detail = docker_cli._docker_op(["docker", "rm", "x"])
        assert ok is False
        assert "unknown flag" in detail and "exit 125" in detail and "docker rm x" in detail
        assert "Usage:" not in detail, "never a help dump in the error message"

    def test_stream_announced_at_info(self, caplog, monkeypatch) -> None:
        fake = _FakePopen(["line"])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
        with caplog.at_level(logging.INFO, logger="docker_app_launcher.docker.command_runner"):
            docker_cli._stream_command(["docker", "compose", "up"], timeout=5.0)
        assert any("exec:" in r.message and "docker compose up" in r.message for r in caplog.records)
