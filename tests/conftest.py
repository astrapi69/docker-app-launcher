"""Shared fixtures. No Docker, no display - everything is mocked or tmp-backed."""

from __future__ import annotations

import contextlib
import logging
import subprocess

# Pre-import tracemalloc at collection time so it is FULLY initialized before
# any test runs. pytest 9's unraisable-exception plugin formats an unraisable
# via ``tracemalloc.get_object_traceback``; if tracemalloc is first imported
# lazily from inside that hook during a GC/teardown, it can be caught mid-import
# ("partially initialized module 'tracemalloc' ... circular import"), and that
# AttributeError becomes a NEW unraisable, re-entering the hook - a cascade that
# fails an unrelated test on loaded CI runners (seen only on Python 3.12).
# Importing it here breaks the cascade at the root.
import tracemalloc as _tracemalloc  # noqa: F401

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
    from docker_app_launcher.docker import detection, inventory, lifecycle

    monkeypatch.setattr(detection, "_api_ping", lambda endpoint=None: ("unavailable", "test isolation"))
    monkeypatch.setattr(inventory, "_api_containers", lambda config, *, running_only: None)
    monkeypatch.setattr(lifecycle, "_get_api_client", lambda: None)


@pytest.fixture(autouse=True)
def _isolate_compose_frontend(monkeypatch):
    """Pin the compose ladder (#48) to 'plugin' so no test ever probes the
    real CLI, and reset the process cache on both sides. Ladder tests
    override ``_probe`` (or ``_run``) themselves."""
    from docker_app_launcher.docker import compose_runtime

    compose_runtime.reset_compose_cache()
    monkeypatch.setattr(compose_runtime, "_probe", lambda config: ("plugin", "test isolation"))
    yield
    compose_runtime.reset_compose_cache()


@pytest.fixture(autouse=True)
def _isolate_tool_versions(monkeypatch):
    """Pin the toolchain probe (#54) to a modern engine/compose/buildx so no
    test shells out to the real docker version commands, and the build
    capability gate passes by default. Version-gate tests override this with
    their own ``ToolVersions``. Cache is reset on both sides."""
    from docker_app_launcher.docker import tool_versions

    tool_versions.reset_versions_cache()
    modern = tool_versions.ToolVersions(
        engine_raw="27.5.1",
        engine=tool_versions.parse_version("27.5.1"),
        cli_raw="27.5.1",
        cli=tool_versions.parse_version("27.5.1"),
        api_raw="1.47",
        api=tool_versions.parse_version("1.47"),
        compose_raw="2.40.2",
        compose=tool_versions.parse_version("2.40.2"),
        buildx_raw="0.20.0",
        buildx=tool_versions.parse_version("0.20.0"),
    )
    monkeypatch.setattr(tool_versions, "_probe_versions", lambda: modern)
    yield
    tool_versions.reset_versions_cache()


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


@pytest.fixture(autouse=True)
def _deterministic_tray(monkeypatch):
    """Pin tray availability to False for every test (#108).

    Tray availability is an ENVIRONMENT fact: a developer desktop has
    AppIndicator, a headless CI runner does not. Three close-behaviour tests
    silently measured that difference instead of the code - green locally,
    red on the runner, in a suite whose whole point is that a verdict means
    the same thing everywhere (contract point 5). Tests that need a tray now
    have to say so, and then they say it for every machine.
    """
    from docker_app_launcher import tray

    monkeypatch.setattr(tray, "tray_available", lambda: False)
