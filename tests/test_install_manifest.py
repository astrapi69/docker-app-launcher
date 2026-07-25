"""Tests for :mod:`docker_app_launcher.install_manifest`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from docker_app_launcher import install_manifest as manifest
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import lifecycle
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


class TestManifest:
    def test_read_missing(self, config) -> None:
        assert manifest.read_manifest(config) is None

    def test_write_and_read(self, config, monkeypatch) -> None:
        monkeypatch.setattr(manifest, "_run", lambda *a, **k: make_result(stdout=""))
        manifest.write_manifest(config, "1.2.3")
        data = manifest.read_manifest(config)
        assert data is not None and data["app_version"] == "1.2.3"
        assert data["app_name"] == "Test App"

    def test_append_history(self, config, monkeypatch) -> None:
        monkeypatch.setattr(manifest, "_run", lambda *a, **k: make_result(stdout=""))
        manifest.write_manifest(config, "1.0.0")
        manifest.append_history(config, "install", "1.0.0")
        data = manifest.read_manifest(config)
        assert data is not None and data["install_history"][-1]["action"] == "install"

    def test_get_version_from_manifest(self, config, monkeypatch) -> None:
        monkeypatch.setattr(manifest, "_run", lambda *a, **k: make_result(stdout=""))
        manifest.write_manifest(config, "9.9.9")
        assert lifecycle.get_version(config) == "9.9.9"

    def test_mark_uninstalled_clears_artifacts(self, config, monkeypatch) -> None:
        monkeypatch.setattr(manifest, "_run", lambda *a, **k: make_result(stdout=""))
        manifest.write_manifest(config, "1.0.0")
        manifest.mark_uninstalled(config, "1.0.0")
        data = manifest.read_manifest(config)
        assert data is not None and data["status"] == "uninstalled"
        assert data["containers"] == []

    def test_manifest_artifacts_excluded_after_uninstall(self, config, monkeypatch) -> None:
        monkeypatch.setattr(manifest, "_run", lambda *a, **k: make_result(stdout=""))
        manifest.write_manifest(config, "1.0.0")
        manifest.mark_uninstalled(config, "1.0.0")
        arts = manifest.manifest_artifacts(config)
        assert arts == {"containers": [], "images": [], "volumes": [], "configs": []}
