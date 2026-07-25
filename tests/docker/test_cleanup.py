"""Tests for :mod:`docker_app_launcher.docker.cleanup`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import shutil
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import cleanup as cleanup


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


class TestCleanup:
    def test_has_stale_artifacts(self) -> None:
        assert cleanup.has_stale_artifacts({"containers": ["x"]}) is True
        assert cleanup.has_stale_artifacts({"containers": [], "images": []}) is False

    def test_cleanup_offer_lines(self, config) -> None:
        lines = cleanup.cleanup_offer_lines(config, {"containers": ["a", "b"], "images": ["i:1"]})
        assert any("2 Container" in line for line in lines)
        assert any("Image(s)" in line for line in lines)

    def test_find_stale_excludes_active(self, config, monkeypatch) -> None:
        monkeypatch.setattr(
            cleanup,
            "manifest_artifacts",
            lambda c: {"containers": ["test-app"], "images": [], "volumes": [], "configs": []},
        )
        monkeypatch.setattr(
            cleanup, "_docker_names", lambda c, kind, pats: ["test-app", "old-app"] if kind == "container" else []
        )
        monkeypatch.setattr(cleanup, "_image_refs", lambda c, pats: [])
        stale = cleanup.find_stale_artifacts(config)
        assert stale["containers"] == ["old-app"]

    def test_find_stale_config_dirs(self, config, tmp_path, monkeypatch) -> None:
        legacy = tmp_path / "legacy-config"
        legacy.mkdir()
        config.cleanup_configs = [str(legacy)]
        monkeypatch.setattr(
            cleanup, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(cleanup, "_docker_names", lambda c, kind, pats: [])
        monkeypatch.setattr(cleanup, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(cleanup, "_running_container_names", lambda c: [])
        stale = cleanup.find_stale_artifacts(config)
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
            cleanup, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(cleanup, "_docker_names", lambda c, kind, pats: [])
        monkeypatch.setattr(cleanup, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(cleanup, "_running_container_names", lambda c: [])
        stale = cleanup.find_stale_artifacts(config)
        assert str(base / ".oldapp") in stale["configs"]
        assert str(base / "oldapp") in stale["configs"]

    def test_find_stale_search_skips_missing_and_live_config(self, config, tmp_path, monkeypatch) -> None:
        base = tmp_path / "base"
        base.mkdir()  # no legacy subdir exists -> nothing found
        config.legacy_names = ["ghost"]
        config.cleanup_search_paths = [str(base)]
        monkeypatch.setattr(
            cleanup, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(cleanup, "_docker_names", lambda c, kind, pats: [])
        monkeypatch.setattr(cleanup, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(cleanup, "_running_container_names", lambda c: [])
        assert cleanup.find_stale_artifacts(config)["configs"] == []

    def _stale_volumes_setup(self, monkeypatch, volumes: list[str]) -> None:
        monkeypatch.setattr(
            cleanup, "manifest_artifacts", lambda c: {"containers": [], "images": [], "volumes": [], "configs": []}
        )
        monkeypatch.setattr(cleanup, "_image_refs", lambda c, pats: [])
        monkeypatch.setattr(cleanup, "_running_container_names", lambda c: [])
        monkeypatch.setattr(cleanup, "_docker_names", lambda c, kind, pats: volumes if kind == "volume" else [])

    def test_find_stale_protects_active_project_volume(self, config, monkeypatch) -> None:
        # The active project's own volume (<project>_*) is NEVER offered; legacy
        # volumes still are - regardless of whether containers currently exist.
        self._stale_volumes_setup(monkeypatch, ["test-app_test-app-data", "bibliogon_bibliogon-data"])
        stale = cleanup.find_stale_artifacts(config)
        assert "test-app_test-app-data" not in stale["volumes"]
        assert "bibliogon_bibliogon-data" in stale["volumes"]

    def test_find_stale_protects_project_volume_even_without_containers(self, config, monkeypatch) -> None:
        # Unconditional: even with no containers (cleanup runs at startup), the
        # active project's data volume is not offered for deletion.
        self._stale_volumes_setup(monkeypatch, ["test-app_test-app-data"])
        assert cleanup.find_stale_artifacts(config)["volumes"] == []

    def test_cleanup_stale_removes_and_reports(self, config, monkeypatch) -> None:
        monkeypatch.setattr(cleanup, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(cleanup, "_docker_op", lambda cmd, **k: (True, ""))
        monkeypatch.setattr(cleanup, "_image_size_bytes", lambda ref: 245_000_000)
        steps: list[str] = []
        ok, msg = cleanup.cleanup_stale(
            config, {"containers": ["old"], "images": ["i:1"], "volumes": [], "configs": []}, on_step=steps.append
        )
        assert ok is True and "2 artifact" in msg
        assert any("245 MB" in s for s in steps)

    def test_cleanup_stale_skips_volumes_by_default(self, config, monkeypatch) -> None:
        monkeypatch.setattr(cleanup, "check_docker", lambda: (True, "ok"))
        removed: list[list[str]] = []

        def fake_op(cmd, **k):
            removed.append(cmd)
            return True, ""

        monkeypatch.setattr(cleanup, "_docker_op", fake_op)
        cleanup.cleanup_stale(config, {"containers": [], "images": [], "volumes": ["v1"], "configs": []})
        assert not any("volume" in cmd for cmd in removed)

    def test_cleanup_logs_every_skipped_volume(self, config, monkeypatch) -> None:
        # No silent gaps: unselected volumes AND active-project volumes each get a line.
        monkeypatch.setattr(cleanup, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(cleanup, "_docker_op", lambda cmd, **k: (True, ""))
        monkeypatch.setattr(cleanup, "_project_volumes", lambda c: ["test-app_test-app-data"])
        steps: list[str] = []
        cleanup.cleanup_stale(
            config,
            {"containers": [], "images": [], "volumes": ["bibliogon_bibliogon-data"], "configs": []},
            on_step=steps.append,
        )
        assert any("bibliogon_bibliogon-data" in s and "not selected" in s for s in steps)
        assert any("test-app_test-app-data" in s and "active project" in s for s in steps)

    def test_cleanup_stale_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(cleanup, "check_docker", lambda: (False, "down"))
        ok, _ = cleanup.cleanup_stale(config, {"containers": ["x"]})
        assert ok is False

    def test_cleanup_removes_config_dir(self, config, tmp_path, monkeypatch) -> None:
        target = tmp_path / "stale-cfg"
        target.mkdir()
        monkeypatch.setattr(cleanup, "check_docker", lambda: (True, "ok"))
        cleanup.cleanup_stale(config, {"containers": [], "images": [], "volumes": [], "configs": [str(target)]})
        assert not target.exists()


@pytest.mark.parametrize(
    ("num", "expected"),
    [(0, "0 B"), (500, "500 B"), (2_000, "2 KB"), (245_000_000, "245 MB"), (3_000_000_000, "3 GB")],
)
def test_human_size(num: int, expected: str) -> None:
    assert cleanup._human_size(num) == expected


class TestRemoveConfigPath:
    def test_removes_file(self, tmp_path) -> None:
        target = tmp_path / "stale.json"
        target.write_text("{}")
        assert cleanup._remove_config_path(str(target)) == (True, "")
        assert not target.exists()

    def test_removes_directory(self, tmp_path) -> None:
        target = tmp_path / "stale-dir"
        (target / "sub").mkdir(parents=True)
        (target / "sub" / "f.txt").write_text("x")
        assert cleanup._remove_config_path(str(target)) == (True, "")
        assert not target.exists()

    def test_nonexistent_is_ok(self, tmp_path) -> None:
        assert cleanup._remove_config_path(str(tmp_path / "gone")) == (True, "")

    def test_oserror_reported(self, tmp_path, monkeypatch) -> None:
        target = tmp_path / "stale-dir"
        target.mkdir()

        def boom(*a, **k):
            raise OSError("permission denied")

        monkeypatch.setattr(shutil, "rmtree", boom)
        ok, detail = cleanup._remove_config_path(str(target))
        assert ok is False and "permission denied" in detail
