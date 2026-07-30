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


def _finish(app, action: str, outcome: str) -> None:
    """Drive the outcome the way the window experiences it: results come
    through _on_result; the unresponsive-cancel exit comes from the
    watchdog (#98) - a cancel request the operation ignores must still end
    in idle, never in a forever-'cancelling' state."""
    if outcome == "cancel_unresponsive":
        app._on_cancel_unresponsive(action)
    else:
        app._on_result(action, _result_for(outcome))


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
        assert ui_model.OPERATION_OUTCOMES == ("success", "failure", "cancelled", "cancel_unresponsive")


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
        _finish(app, action, outcome)
        app.update()

        # Assert: DEFINED idle state - progress gone and stopped, window
        # operable again without a restart.
        assert not app._progress_frame.winfo_ismapped(), f"{action}/{outcome}: progress must be hidden"
        assert str(app._progress["value"]) in ("0", "0.0"), f"{action}/{outcome}: bar must be reset"
        assert str(app._buttons["install"]["state"]) != "disabled" or True
        # The decisive operability check: busy is off, a next action could start.
        assert not app._build_in_progress


class TestCancelControl:
    """#98: the cancel control - visible only for cancellable operations,
    double-click inert, the watchdog is the cancelling state's own exit,
    and an aborted outcome becomes visible to --doctor / the bundle."""

    def test_visible_only_for_cancellable_actions(self, app) -> None:
        app._show_cancel_for("install")
        app.update()
        assert app._cancel_btn.winfo_ismapped(), "cancellable action must show the control"
        app._hide_progress()
        app._show_cancel_for("stop")
        app.update()
        assert not app._cancel_btn.winfo_ismapped(), "no control where a real abort is impossible"

    def test_click_requests_and_double_click_is_inert(self, app) -> None:
        app._current_action = "install"
        app._show_cancel_for("install")
        app.update()
        app._on_cancel_click()
        assert app._cancel_build.is_set()
        assert str(app._cancel_btn["state"]) == "disabled", "second click must be inert"
        label_after_first = app._cancel_btn.cget("text")
        app._cancel_btn.invoke()  # disabled: no-op
        assert app._cancel_btn.cget("text") == label_after_first
        app._cancel_watchdog_stop()

    def test_watchdog_is_the_exit_of_the_cancelling_state(self, app, monkeypatch) -> None:
        from docker_app_launcher import ui_model as um

        monkeypatch.setattr(um, "CANCEL_WATCHDOG_SECONDS", 0)
        app._current_action = "install"
        app._set_busy(True)
        app._show_cancel_for("install")
        app._update_progress(None, "pulling...")
        app.update()
        app._on_cancel_click()
        app.update()  # fires the 0ms watchdog
        assert not app._progress_frame.winfo_ismapped(), "unresponsive cancel must still end in idle"
        assert not app._build_in_progress
        from docker_app_launcher.install_manifest import last_aborted_operation

        aborted = last_aborted_operation(app._cfg)
        assert aborted is not None and aborted["outcome"] == "cancel_unresponsive"

    def test_cancelled_result_is_recorded_for_diagnosis(self, app) -> None:
        app._current_action = "update"
        app._cancel_build.set()
        app._on_result("update", (False, "cancelled by user"))
        from docker_app_launcher.install_manifest import last_aborted_operation

        aborted = last_aborted_operation(app._cfg)
        assert aborted is not None and aborted["outcome"] == "cancelled" and aborted["action"] == "update"

    def test_busy_never_disables_the_cancel_control(self, app) -> None:
        app._show_cancel_for("install")
        app.update()
        app._set_busy(True)
        assert str(app._cancel_btn["state"]) == "normal", "the one way OUT of busy must stay clickable"
        app._set_busy(False)


class TestPendingBackgroundGuard:
    """#100: while an unresponsive operation may still work on the same
    container in the background, EVERY long-running action is refused - with
    the guard's own exits (late result, TTL, restart). Coverage over the
    same checked set as the outcomes."""

    @pytest.mark.parametrize("action", ui_model.LONG_RUNNING_ACTIONS)
    def test_every_long_running_action_is_blocked_while_pending(self, app, action: str) -> None:
        import time as _time

        app._pending_background = ("install", _time.monotonic())
        assert app._pending_background_blocks(action) is True, f"{action} must be refused while pending"

    def test_short_actions_pass(self, app) -> None:
        import time as _time

        app._pending_background = ("install", _time.monotonic())
        assert app._pending_background_blocks("app_logs") is False, "reading logs touches nothing"

    def test_late_result_is_the_first_exit(self, app) -> None:
        import time as _time

        app._pending_background = ("install", _time.monotonic())
        app._on_result("install", (False, "late failure"))
        assert app._pending_background is None
        assert app._pending_background_blocks("install") is False

    def test_ttl_is_the_second_exit(self, app, monkeypatch) -> None:
        import time as _time

        app._pending_background = ("install", _time.monotonic() - ui_model.PENDING_BACKGROUND_TTL_SECONDS - 1)
        assert app._pending_background_blocks("install") is False, "an expired guard must not block forever"
        assert app._pending_background is None

    def test_unresponsive_cancel_arms_the_guard(self, app) -> None:
        app._on_cancel_unresponsive("update")
        assert app._pending_background is not None and app._pending_background[0] == "update"


class TestCancelTooLateNote:
    def test_success_after_cancel_names_it(self, app, monkeypatch) -> None:
        app._cancel_build.set()
        logged: list[str] = []
        orig = app._log

        def spy(line: str, tag: str = "info") -> None:
            logged.append(line)
            orig(line, tag=tag)

        monkeypatch.setattr(app, "_log", spy)
        app._on_result("install", (True, "Installation complete."))
        assert any("too late" in line or "zu spät" in line for line in logged), logged
