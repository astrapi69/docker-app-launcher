"""REAL docker-daemon signal tests - run only inside the dedicated container.

Launched by ``run_docker_signal_integration.sh``: a privileged throwaway
container with a REAL dockerd. Unlike TestReloginTransitionSimulation (which
injects the permission signal and only validates its PROCESSING), these
tests validate the signal GENERATION: the actual CLI + socket behaviour of a
running daemon against a user without docker-group membership, and the
counter-direction with the daemon stopped. This is the class the v0.16.0
device verification proved untested (#27 reopened).
"""

from __future__ import annotations

import getpass
import os
import subprocess

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_DOCKER_SIGNAL_INTEGRATION") != "1",
        reason="real-daemon integration: only inside the dedicated container",
    ),
]


def test_running_daemon_without_group_classifies_as_permission() -> None:
    from docker_app_launcher import actions
    from docker_app_launcher.config import LauncherConfig

    assert getpass.getuser() == "daltest"
    ok, msg = actions.check_docker()
    assert ok is False
    assert "usermod -aG docker" in msg, f"real EACCES misclassified: {msg!r}"
    assert "not started" not in msg

    info = actions.check_docker_detailed(LauncherConfig(app_name="X", locale="en").resolve())
    assert info["can_fix_permission"] is True
    assert info["can_start"] is False


def test_stopped_daemon_classifies_as_down() -> None:
    from docker_app_launcher import actions
    from docker_app_launcher.config import LauncherConfig

    # The runner stops dockerd before this test module ordering? No - this
    # test asks the runner via a marker file written after the stop phase.
    if not os.path.exists("/tmp/dal-daemon-stopped"):
        pytest.skip("daemon still running (first phase)")
    ok, msg = actions.check_docker()
    assert ok is False and "not started" in msg
    info = actions.check_docker_detailed(LauncherConfig(app_name="X", locale="en").resolve())
    assert info["can_fix_permission"] is False
    assert info["can_start"] is True


def test_raw_cli_signal_is_recorded_for_the_protocol() -> None:
    """Document the raw stderr this environment produces - diagnosis data."""
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    print(f"RAW-SIGNAL rc={result.returncode}: {(result.stderr or '').splitlines()[:1]}")
    assert result.returncode != 0  # daltest has no access either way
