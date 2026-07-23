"""Shared fixtures. No Docker, no display - everything is mocked or tmp-backed."""

from __future__ import annotations

import contextlib
import logging
import subprocess

import pytest

from docker_app_launcher.config import LauncherConfig


@pytest.fixture(autouse=True)
def reset_root_logging():
    """Restore the root logger after each test.

    ``__main__.main`` / ``logging_setup.setup_logging`` add handlers to the
    root logger; without this they would accumulate across tests (leaking
    file descriptors and duplicating log output into ``capsys``).
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for handler in root.handlers[:]:
        if handler not in saved_handlers:
            root.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()
    root.setLevel(saved_level)


@pytest.fixture(autouse=True)
def _force_en_locale(monkeypatch):
    """Pin ``"auto"`` locale resolution to English so string assertions are
    host-independent. Tests needing another locale set ``locale=...`` explicitly.
    """
    monkeypatch.setattr("docker_app_launcher.config.detect_system_locale", lambda: "en")


@pytest.fixture(autouse=True)
def isolate_home(tmp_path, monkeypatch):
    """Give every test its own HOME so config/manifest writes stay isolated."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("APPDATA", raising=False)
    return home


@pytest.fixture
def config(tmp_path):
    """A resolved config pointing at a tmp config dir and install dir."""
    cfg = LauncherConfig(
        app_name="Test App",
        container_name="test-app",
        default_port=8080,
        config_dir=str(tmp_path / ".test-app"),
        install_dir=str(tmp_path / "repo"),
        locale="en",  # pin so string assertions don't depend on the host locale
    )
    cfg.resolve()
    return cfg


def make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    """Build a fake ``subprocess.CompletedProcess`` for mocking ``actions._run``."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def docker_ok(monkeypatch):
    """Make ``check_docker`` report a running daemon."""
    from docker_app_launcher import actions

    monkeypatch.setattr(actions, "check_docker", lambda: (True, "Docker is running."))


@pytest.fixture(autouse=True)
def _reset_docker_host_override():
    """Reset the #25 context-fallback override on BOTH sides of every test
    (module-level state must not leak across test boundaries)."""
    from docker_app_launcher import actions

    actions._reset_docker_host_override()
    yield
    actions._reset_docker_host_override()
