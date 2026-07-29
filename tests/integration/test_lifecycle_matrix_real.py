"""REAL lifecycle matrix (#79): the full operation set per deployment mode.

Launched by ``run_lifecycle_matrix_integration.sh`` against the LOCAL Docker
daemon. For each of the three modes - image, dockerfile, compose - a tiny
fixture app is driven through the COMPLETE operation set and the transitions
between states, because the mocked suite cannot catch cross-layer breaks by
nature (proven twice: the #77 sentinel was correct for the build path and
broke the pull path; the #78 dispatch fell from a successful image acquire
into the compose build):

    install -> install again (already installed) -> logs -> stop ->
    start (restart a STOPPED stack) -> stop -> uninstall ->
    stop/uninstall when nothing runs

The checked set is enumerated in ``_OPERATIONS`` and asserted complete at
the end of every mode run - no silently skipped operation.

Gates: ``DAL_LIFECYCLE_MATRIX=1`` (never runs in the Docker-free suite);
``DAL_LIFECYCLE_MATRIX_MODE`` optionally narrows to one mode. Runtime split
(documented in docs/environment-matrix.md): the per-push CI runs the unit
suite and the old-engine cell; this full matrix runs nightly and on demand.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_LIFECYCLE_MATRIX") != "1",
        reason="real lifecycle matrix: only via run_lifecycle_matrix_integration.sh",
    ),
]

_ONLY = os.environ.get("DAL_LIFECYCLE_MATRIX_MODE", "")

_DOCKERFILE = """\
FROM busybox:1.36.1
WORKDIR /www
COPY index.html /www/
EXPOSE 80
CMD ["busybox", "httpd", "-f", "-p", "80", "-h", "/www"]
"""

_COMPOSE = """\
services:
  app:
    build: .
    container_name: {container}
    ports:
      - "${{APP_PORT:-{port}}}:80"
"""

# The operation set every mode must pass - the proof of the checked set.
_OPERATIONS = [
    "install",
    "install_when_installed",
    "app_logs",
    "stop",
    "start_stopped_stack",
    "stop_again",
    "uninstall",
    "stop_when_nothing_runs",
]


# Real fixtures against the real daemon: the conftest isolation fixtures are
# shadowed by these same-named no-ops so detection/inventory/compose/tool
# probes hit the actual system (that is the point of the matrix).
@pytest.fixture(autouse=True)
def _isolate_docker_api() -> None:
    return None


@pytest.fixture(autouse=True)
def _isolate_compose_frontend():
    from docker_app_launcher.docker import compose_runtime

    compose_runtime.reset_compose_cache()
    yield
    compose_runtime.reset_compose_cache()


@pytest.fixture(autouse=True)
def _isolate_tool_versions():
    from docker_app_launcher.docker import tool_versions

    tool_versions.reset_versions_cache()
    yield
    tool_versions.reset_versions_cache()


def _mode_config(mode: str, tmp_path: Path):
    from docker_app_launcher.config import LauncherConfig

    port = {"image": 18131, "dockerfile": 18132, "compose": 18133}[mode]
    container = f"dal-matrix-{mode}"
    install_dir = tmp_path / mode
    install_dir.mkdir(parents=True, exist_ok=True)
    if mode in ("dockerfile", "compose"):
        (install_dir / "index.html").write_text('{"status": "ok"}\n', encoding="utf-8")
        (install_dir / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    if mode == "compose":
        (install_dir / "docker-compose.prod.yml").write_text(
            _COMPOSE.format(container=container, port=port), encoding="utf-8"
        )
    return LauncherConfig(
        app_name=f"Matrix {mode}",
        container_name=container,
        compose_project=container,
        image_name=container,
        deployment_mode=mode,
        image_reference="traefik/whoami:v1.10" if mode == "image" else "",
        install_dir=str(install_dir),
        config_dir=str(tmp_path / f".{container}"),
        default_port=port,
        # image/dockerfile publish host_port -> 80 (the fixtures listen on 80);
        # compose maps the port in the yaml itself.
        container_port=80 if mode in ("image", "dockerfile") else 0,
        health_check_path="/",
        health_check_key="",  # HTTP 200 is the contract for the fixtures
        health_check_timeout=60,
        locale="en",
    ).resolve()


def _drive_full_operation_set(mode: str, tmp_path: Path) -> None:
    from docker_app_launcher.docker import lifecycle

    config = _mode_config(mode, tmp_path)
    checked: list[str] = []
    lifecycle.uninstall(config)  # clean slate, best effort

    ok, msg = lifecycle.install(config)
    assert ok, f"[{mode}] install failed: {msg}"
    assert lifecycle.get_state(config) == "running", f"[{mode}] not running after install"
    checked.append("install")

    ok, msg = lifecycle.install(config)
    assert ok, f"[{mode}] install-when-installed must be graceful: {msg}"
    checked.append("install_when_installed")

    ok, text = lifecycle.app_logs(config)
    assert ok, f"[{mode}] app_logs failed: {text}"
    checked.append("app_logs")

    ok, msg = lifecycle.stop(config)
    assert ok, f"[{mode}] stop failed: {msg}"
    assert lifecycle.get_state(config) == "stopped", f"[{mode}] not stopped after stop"
    checked.append("stop")

    ok, msg = lifecycle.start(config)
    assert ok, f"[{mode}] restarting the stopped stack failed: {msg}"
    assert lifecycle.get_state(config) == "running", f"[{mode}] not running after restart"
    checked.append("start_stopped_stack")

    ok, msg = lifecycle.stop(config)
    assert ok, f"[{mode}] second stop failed: {msg}"
    checked.append("stop_again")

    ok, msg = lifecycle.uninstall(config)
    assert ok, f"[{mode}] uninstall failed: {msg}"
    assert lifecycle.get_state(config) == "not_installed", f"[{mode}] leftovers after uninstall"
    checked.append("uninstall")

    ok, msg = lifecycle.stop(config)
    assert not ok, f"[{mode}] stop with nothing running must say so, got ok: {msg}"
    checked.append("stop_when_nothing_runs")

    assert checked == _OPERATIONS, f"[{mode}] operation set incomplete: {checked}"


def _skip_unless(mode: str) -> None:
    if _ONLY and mode != _ONLY:
        pytest.skip(f"DAL_LIFECYCLE_MATRIX_MODE={_ONLY} narrows this run")


class TestImageModeLifecycle:
    def test_full_operation_set(self, tmp_path: Path) -> None:
        _skip_unless("image")
        _drive_full_operation_set("image", tmp_path)


class TestDockerfileModeLifecycle:
    def test_full_operation_set(self, tmp_path: Path) -> None:
        _skip_unless("dockerfile")
        _drive_full_operation_set("dockerfile", tmp_path)


class TestComposeModeLifecycle:
    def test_full_operation_set(self, tmp_path: Path) -> None:
        _skip_unless("compose")
        _drive_full_operation_set("compose", tmp_path)
