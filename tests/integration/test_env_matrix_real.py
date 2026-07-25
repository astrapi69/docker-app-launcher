"""REAL environment-matrix tests - run only inside the dedicated containers.

Launched by ``run_env_matrix_integration.sh``: each scenario provisions a
throwaway container with a specific Docker environment (see
``docs/environment-matrix.md``) and asserts the launcher's DETECTION /
READINESS layer against a REAL CLI and daemon - never a real image build, so
the scenarios stay fast and deterministic. This extends the #27 signal
harness (permission-vs-down against a real socket) to the environment cells
the environment-matrix audit flagged as previously mock-only.

Each test is gated twice: by ``DAL_ENV_MATRIX_INTEGRATION=1`` (so it never
runs in the normal, Docker-free suite) and by ``DAL_ENV_MATRIX_SCENARIO``
(so the runner points one container at one cell).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_ENV_MATRIX_INTEGRATION") != "1",
        reason="real-environment matrix: only inside the dedicated container",
    ),
]

_SCENARIO = os.environ.get("DAL_ENV_MATRIX_SCENARIO", "")


def _config(tmp_path: Path):
    from docker_app_launcher.config import LauncherConfig

    return LauncherConfig(
        app_name="Matrix", container_name="dal-matrix", locale="en", install_dir=str(tmp_path)
    ).resolve()


@pytest.mark.skipif(_SCENARIO != "no_compose", reason="scenario gate: no_compose")
def test_no_compose_frontend_is_actionable_before_build(tmp_path: Path) -> None:
    """Engine present (docker.io ships NO compose plugin and NO v1), daemon up.

    The #48 ladder must turn the plugin-free device class into an actionable
    install hint BEFORE any build, never the raw ``unknown shorthand flag``
    help dump the CLI would otherwise emit.
    """
    from docker_app_launcher import actions

    cfg = _config(tmp_path)
    cfg.compose_path.write_text("services: {}\n")  # a project exists; the frontend does not
    ok, msg = actions.install(cfg)
    assert ok is False
    assert "docker-compose-plugin" in msg or "Compose" in msg, msg
    # the exact 20.10-CLI failure signature must NOT leak into the message (#48/#49)
    assert "unknown shorthand flag" not in msg
    assert "See 'docker --help'" not in msg


@pytest.mark.skipif(_SCENARIO != "no_docker", reason="scenario gate: no_docker")
def test_docker_binary_absent_is_reported_as_not_installed(tmp_path: Path) -> None:
    """No ``docker`` binary at all: a clear not-installed verdict with a hint."""
    from docker_app_launcher import actions

    ok, msg = actions.check_docker()
    assert ok is False
    assert "not installed" in msg.lower() or "not in path" in msg.lower(), msg

    detailed = actions.check_docker_detailed(_config(tmp_path))
    assert detailed["installed"] is False
    assert detailed["command"], "a not-installed verdict should offer an install command"


@pytest.mark.skipif(_SCENARIO != "no_group", reason="scenario gate: no_group")
def test_socket_permission_offers_group_fix_on_a_local_unix_socket(tmp_path: Path) -> None:
    """The ONE cell where ``usermod -aG docker`` is correct advice: a local
    root unix socket, user not in the docker group. (The rootless / remote /
    Desktop cells where it is WRONG advice are tracked as a gap issue and
    verified by their own scenarios once the endpoint-kind gate lands.)"""
    import getpass

    from docker_app_launcher import actions

    assert getpass.getuser() == "daltest"
    ok, msg = actions.check_docker()
    assert ok is False
    assert "usermod -aG docker" in msg and "not started" not in msg, msg
    detailed = actions.check_docker_detailed(_config(tmp_path))
    assert detailed["can_fix_permission"] is True
