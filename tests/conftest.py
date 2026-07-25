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
def _isolate_docker_api(monkeypatch):
    """Route every test through the CLI code path by default (#44).

    ``detection._api_ping`` would otherwise hit the REAL daemon socket via
    docker-py — tests must never talk to Docker. API-path tests override
    this per-test; ``test_py_client`` is unaffected (it stubs the docker
    module itself, not this indirection).
    """
    from docker_app_launcher.docker import detection, inventory

    monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("unavailable", "test isolation"))
    monkeypatch.setattr(inventory, "_api_containers", lambda config, *, running_only: None)


@pytest.fixture(autouse=True)
def _reset_docker_host_override():
    """Reset the #25 context-fallback override on BOTH sides of every test
    (module-level state must not leak across test boundaries)."""
    from docker_app_launcher import actions

    actions._reset_docker_host_override()
    yield
    actions._reset_docker_host_override()


def _prefer_invisible_display() -> None:
    """Route every GUI test to the containerized Xvfb when it is reachable.

    Test windows must never appear on a developer's desktop. The off-screen
    helper in test_gui_window covers plain Tk, but CustomTkinter maps its
    window DURING __init__ - before any test code can reposition it - so the
    only complete fix is a display humans cannot see. ``make xvfb-up`` starts
    Xvfb in a docker container on localhost:6099 (TCP, snap-docker cannot
    share /tmp/.X11-unix); when that port answers, all GUI tests use it.
    DAL_SHOW_TEST_WINDOWS=1 keeps the real display for debugging.
    """
    import os
    import socket

    if os.environ.get("DAL_SHOW_TEST_WINDOWS"):
        return
    try:
        with socket.create_connection(("127.0.0.1", 6099), timeout=0.3):
            pass
    except OSError:
        return
    os.environ["DISPLAY"] = "127.0.0.1:99"


_prefer_invisible_display()
