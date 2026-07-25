"""Tests for :mod:`docker_app_launcher.docker.inventory`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import inventory as inventory
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


class TestProjectContainers:
    def test_parses_id_name_pairs(self, config, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout="abc\tapp-web\ndef\tapp-db\n"))
        pairs = inventory._project_containers(config, running_only=False)
        assert pairs == [("abc", "app-web"), ("def", "app-db")]

    def test_missing_name_falls_back_to_id(self, config, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout="abc\t\n"))
        assert inventory._project_containers(config, running_only=False) == [("abc", "abc")]

    def test_running_only_omits_dash_a(self, config, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            return make_result()

        monkeypatch.setattr(inventory, "_run", fake_run)
        inventory._project_containers(config, running_only=True)
        assert "-a" not in seen["cmd"]
        inventory._project_containers(config, running_only=False)
        assert "-a" in seen["cmd"]

    def test_docker_missing_returns_empty(self, config, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert inventory._project_containers(config, running_only=False) == []

    def test_timeout_returns_empty(self, config, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(inventory, "_run", boom)
        assert inventory._project_containers(config, running_only=True) == []


class TestProjectImages:
    def test_parses_and_dedupes_by_id(self, config, monkeypatch) -> None:
        stdout = "id1\trepo/app\nid1\trepo/app-alias\nid2\trepo/db\n"
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout=stdout))
        assert inventory._project_images(config) == [("id1", "repo/app"), ("id2", "repo/db")]

    def test_missing_ref_falls_back_to_id(self, config, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout="id1\t\n"))
        assert inventory._project_images(config) == [("id1", "id1")]

    def test_uses_image_patterns_as_reference_filters(self, config, monkeypatch) -> None:
        seen: dict[str, list[str]] = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            return make_result()

        monkeypatch.setattr(inventory, "_run", fake_run)
        inventory._project_images(config)
        filters = [seen["cmd"][i + 1] for i, arg in enumerate(seen["cmd"]) if arg == "--filter"]
        assert filters, "expected at least one --filter reference=..."
        assert all(f.startswith("reference=*") for f in filters)

    def test_docker_missing_returns_empty(self, config, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert inventory._project_images(config) == []


class TestDockerNames:
    def test_container_kind_uses_ps(self, config, monkeypatch) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **k):
            seen.append(cmd)
            return make_result(stdout="app-web\n")

        monkeypatch.setattr(inventory, "_run", fake_run)
        names = inventory._docker_names(config, "container", ("app",))
        assert names == ["app-web"]
        assert seen[0][:3] == ["docker", "ps", "-a"]

    def test_volume_kind_uses_volume_ls(self, config, monkeypatch) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **k):
            seen.append(cmd)
            return make_result(stdout="app-data\n")

        monkeypatch.setattr(inventory, "_run", fake_run)
        names = inventory._docker_names(config, "volume", ("app",))
        assert names == ["app-data"]
        assert seen[0][:4] == ["docker", "volume", "ls", "--format"]

    def test_dedupes_across_patterns(self, config, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout="same\n"))
        assert inventory._docker_names(config, "container", ("a", "b")) == ["same"]

    def test_empty_pattern_skipped(self, config, monkeypatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **k):
            calls.append(cmd)
            return make_result()

        monkeypatch.setattr(inventory, "_run", fake_run)
        inventory._docker_names(config, "container", ("", "app"))
        assert len(calls) == 1

    def test_error_on_one_pattern_continues(self, config, monkeypatch) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd="docker", timeout=15)
            return make_result(stdout="found\n")

        monkeypatch.setattr(inventory, "_run", fake_run)
        assert inventory._docker_names(config, "container", ("a", "b")) == ["found"]


class TestImageSizeBytes:
    def test_valid_size(self, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout="123456789\n"))
        assert inventory._image_size_bytes("repo/app:latest") == 123456789

    def test_nonzero_returncode(self, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(returncode=1, stderr="no such image"))
        assert inventory._image_size_bytes("gone") == 0

    def test_non_numeric_output(self, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: make_result(stdout="not-a-number"))
        assert inventory._image_size_bytes("weird") == 0

    def test_docker_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(inventory, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert inventory._image_size_bytes("x") == 0

    def test_timeout(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(inventory, "_run", boom)
        assert inventory._image_size_bytes("x") == 0
