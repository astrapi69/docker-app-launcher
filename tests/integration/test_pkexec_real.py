"""REAL pkexec integration test - runs only inside the throwaway container.

Launched by ``tests/integration/run_pkexec_integration.sh``: an isolated
Ubuntu container with polkitd running and a narrow polkit rule that
auto-approves exactly ``usermod`` for exactly the ``daltest`` user - so the
REAL ``pkexec usermod -aG docker $USER`` executes end to end, no subprocess
mock anywhere. Guarded by ``DAL_PKEXEC_INTEGRATION=1`` so a normal test run
(dev machine, CI unit job) always skips it.

HONEST LIMIT: this proves the pkexec invocation, the polkit authorization
path, the usermod execution and the getent verification work for real. It
does NOT prove that the interactive polkit password dialog appears and is
usable on a real desktop session - that structural difference remains a
manual test.

State: the container is started with ``--rm``; nothing survives the run, so
"reset after the test" is the container's lifecycle itself.
"""

from __future__ import annotations

import getpass
import os
import subprocess

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_PKEXEC_INTEGRATION") != "1",
        reason="real-pkexec integration: only inside the dedicated container (run_pkexec_integration.sh)",
    ),
]


def test_real_pkexec_adds_the_group_and_still_demands_relogin() -> None:
    from docker_app_launcher import actions
    from docker_app_launcher.config import LauncherConfig

    assert getpass.getuser() == "daltest", "must run as the dedicated test user, never a real account"

    config = LauncherConfig(app_name="Integration", locale="en").resolve()
    ok, message = actions.add_user_to_docker_group(config)

    assert ok is True, f"real pkexec flow failed: {message}"
    # Independent proof, not just the function's own verification:
    out = subprocess.run(["getent", "group", "docker"], capture_output=True, text=True, check=True).stdout
    assert "daltest" in out
    # The success message must still demand the re-login - the group change
    # is not active in this session, and the message must never claim ready.
    assert "log out" in message.lower()
    assert "ready" not in message.lower()


def test_second_run_is_idempotent() -> None:
    from docker_app_launcher import actions
    from docker_app_launcher.config import LauncherConfig

    config = LauncherConfig(app_name="Integration", locale="en").resolve()
    ok, message = actions.add_user_to_docker_group(config)
    assert ok is True, f"usermod -aG must be idempotent: {message}"
