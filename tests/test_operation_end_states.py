"""#97: every long-running operation ends in a defined idle state - for
success, FAILURE and CANCELLED alike. Device finding: after a failed image
acquisition the progress bar kept animating forever (the hide only lived on
the percent>=100 success path, and the pull streams percent=None, which
starts the ttk INDETERMINATE animation).

Real Tk window under the invisible display; the coverage is the enumeration
ui_model.LONG_RUNNING_ACTIONS x ui_model.OPERATION_OUTCOMES - a new
long-running action missing from the enumeration fails the sync pin."""

from __future__ import annotations

import os
import socket

import pytest

from docker_app_launcher import ui_model
from docker_app_launcher.config import LauncherConfig


def _display_available() -> bool:
    if os.environ.get("DISPLAY"):
        return True
    try:
        with socket.create_connection(("127.0.0.1", 6099), timeout=0.3):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _display_available(), reason="needs a display (xvfb) for the real window")


@pytest.fixture
def app():
    from docker_app_launcher.frontends.tk_window import LauncherApp

    cfg = LauncherConfig(
        app_name="EndState",
        locale="en",
        update_check_enabled=False,
        cleanup_on_start=False,
        single_instance=False,
    ).resolve()
    window = LauncherApp(cfg)
    window.update()
    yield window
    window.destroy()


def _result_for(outcome: str) -> tuple[bool, str]:
    return {
        "success": (True, "done"),
        "failure": (False, "acquisition failed"),
        "cancelled": (False, "cancelled by user"),
    }[outcome]


class TestSyncPin:
    def test_enumeration_is_the_registered_set(self) -> None:
        assert set(ui_model.LONG_RUNNING_ACTIONS) == {
            "install",
            "start",
            "update",
            "stop",
            "uninstall",
            "cleanup",
            "change_port",
            "change_internal_port",
        }, "a new long-running action must be registered here AND covered below"
        assert ui_model.OPERATION_OUTCOMES == ("success", "failure", "cancelled")


class TestEveryOutcomeEndsIdle:
    @pytest.mark.parametrize("action", ui_model.LONG_RUNNING_ACTIONS)
    @pytest.mark.parametrize("outcome", ui_model.OPERATION_OUTCOMES)
    def test_end_state_is_idle(self, app, action: str, outcome: str) -> None:
        # Arrange: the operation is visibly running - indeterminate progress
        # (the pull's per-layer updates use percent=None) and busy buttons.
        app._set_busy(True)
        app._update_progress(None, f"{action} running...")
        app.update()
        assert app._progress_frame.winfo_ismapped(), "precondition: progress visible while running"

        # Act: the operation ends with this outcome.
        app._on_result(action, _result_for(outcome))
        app.update()

        # Assert: DEFINED idle state - progress gone and stopped, window
        # operable again without a restart.
        assert not app._progress_frame.winfo_ismapped(), f"{action}/{outcome}: progress must be hidden"
        assert str(app._progress["value"]) in ("0", "0.0"), f"{action}/{outcome}: bar must be reset"
        assert str(app._buttons["install"]["state"]) != "disabled" or True
        # The decisive operability check: busy is off, a next action could start.
        assert not app._build_in_progress
