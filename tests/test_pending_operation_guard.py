"""#102: the pending-operation guard must hold on BOTH documented entry
paths. Mirror class of the bundle finding - one guard, one shared layer,
two callers; two implementations of the same guard drift.

The marker is PID-bound: the hung worker is a thread of the GUI process,
so a dead PID voids the marker (that also makes "restart clears it"
mechanically true, and a crashed GUI never blocks forever)."""

from __future__ import annotations

import json
import os
import time
import typing

import pytest

from docker_app_launcher import __main__, actions, lockfile, ui_model
from docker_app_launcher.config import LauncherConfig


@pytest.fixture
def gconfig(tmp_path):
    return LauncherConfig(
        app_name="Guard App",
        config_dir=str(tmp_path / ".guard-app"),
        install_dir=str(tmp_path / "repo"),
        locale="en",
    ).resolve()


def _cfg_file(gconfig, tmp_path) -> str:
    path = tmp_path / "launcher.json"
    gconfig.to_json(path)
    return str(path)


class TestMarkerLifecycle:
    def test_write_read_clear(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "install")
        marker, degraded = lockfile.read_pending_operation(gconfig)
        assert degraded is None
        assert marker is not None and marker["action"] == "install" and int(str(marker["pid"])) == os.getpid()
        lockfile.clear_pending_operation(gconfig)
        assert lockfile.read_pending_operation(gconfig) == (None, None)

    def test_dead_pid_voids_the_marker(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "update")
        path = gconfig.config_path / "pending-operation.json"
        data = json.loads(path.read_text())
        data["pid"] = 999999900  # certainly dead
        path.write_text(json.dumps(data))
        assert lockfile.read_pending_operation(gconfig) == (None, None), (
            "a dead owner means the hung worker died with it - the marker is void"
        )

    def test_malformed_marker_is_degraded_not_a_crash(self, gconfig) -> None:
        path = gconfig.config_path / "pending-operation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken")
        marker, degraded = lockfile.read_pending_operation(gconfig)
        assert marker is None and degraded is not None


class TestSharedGate:
    def test_blocks_long_running_while_pending(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "install")
        block, note = ui_model.check_pending_operation(gconfig, "update")
        assert block is not None and "install" in block
        assert note is None

    def test_short_actions_pass(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "install")
        assert ui_model.check_pending_operation(gconfig, "app_logs") == (None, None)

    def test_expiry_is_release_without_all_clear(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "install")
        path = gconfig.config_path / "pending-operation.json"
        data = json.loads(path.read_text())
        data["at"] = time.time() - ui_model.PENDING_BACKGROUND_TTL_SECONDS - 1
        path.write_text(json.dumps(data))
        block, note = ui_model.check_pending_operation(gconfig, "install")
        assert block is None, "the TTL is the guard's exit"
        assert note is not None and ("never confirmed" in note or "nie" in note), (
            "a release BY TIME must say the previous state was never confirmed - it is not an all-clear"
        )
        assert lockfile.read_pending_operation(gconfig) == (None, None), "expired marker is consumed"


class TestCliSeesTheGuard:
    """The RED of this order: today the CLI runs the action although the
    marker is armed - the guard was window memory only."""

    _FLAGS: typing.ClassVar[dict[str, str]] = {
        "install": "--install",
        "start": "--start",
        "update": "--update",
        "stop": "--stop",
        "uninstall": "--uninstall",
    }

    @pytest.mark.parametrize("action", sorted(_FLAGS))
    def test_cli_flag_is_blocked_while_pending(self, gconfig, tmp_path, monkeypatch, action, capsys) -> None:
        called: list[str] = []

        def fake_action(c: object, **k: object) -> tuple[bool, str]:
            called.append(action)
            return True, "ran"

        monkeypatch.setattr(actions, action, fake_action)
        lockfile.write_pending_operation(gconfig, "install")
        rc = __main__.main(["--config", _cfg_file(gconfig, tmp_path), self._FLAGS[action]])
        assert called == [], f"{action}: the CLI must not run past the armed guard"
        assert rc == 1
        assert "install" in capsys.readouterr().out.lower()

    def test_cli_proceeds_with_expiry_note_after_ttl(self, gconfig, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(actions, "stop", lambda c, **k: (True, "stopped"))
        lockfile.write_pending_operation(gconfig, "install")
        path = gconfig.config_path / "pending-operation.json"
        data = json.loads(path.read_text())
        data["at"] = time.time() - ui_model.PENDING_BACKGROUND_TTL_SECONDS - 1
        path.write_text(json.dumps(data))
        rc = __main__.main(["--config", _cfg_file(gconfig, tmp_path), "--stop"])
        out = capsys.readouterr().out
        assert rc == 0 and ("never confirmed" in out or "nie" in out)


class TestGuardedSetSyncPin:
    def test_cli_guard_covers_every_long_running_action_with_a_flag(self) -> None:
        # One source: a new CLI flag for a long-running action must join the
        # guarded map, or this pin goes red. change_port/change_internal_port
        # have no standalone CLI action flag (documented exception).
        assert set(__main__.GUARDED_CLI_ACTIONS) == set(ui_model.LONG_RUNNING_ACTIONS) - {
            "change_port",
            "change_internal_port",
        }


class TestDeliberateOpenIsVisible:
    """#103: the NAMED exception to contract point 3. This guard must not
    fail closed - an unreadable marker would brick the launcher - but a
    SILENT open is the worst case: a protection missing unnoticed. Both
    degraded sides carry a visible note and the action proceeds."""

    def test_unreadable_marker_opens_with_note(self, gconfig) -> None:
        import os
        import stat

        path = gconfig.config_path / "pending-operation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"action": "install", "at": 9999999999, "pid": 1}')
        os.chmod(path, 0)
        try:
            block, note = ui_model.check_pending_operation(gconfig, "install")
            assert block is None, "the guard must not brick the launcher"
            assert note is not None and ("guard" in note.lower() or "schutz" in note.lower()), (
                "a silent open is the worst case - the missing protection must be named"
            )
        finally:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def test_garbage_marker_opens_with_note_and_is_consumed(self, gconfig) -> None:
        path = gconfig.config_path / "pending-operation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken")
        block, note = ui_model.check_pending_operation(gconfig, "install")
        assert block is None and note is not None
        assert not path.exists(), "consumed, so the note does not repeat forever"

    def test_unwritable_dir_reports_the_failed_arming(self, gconfig) -> None:
        import os
        import stat

        gconfig.config_path.mkdir(parents=True, exist_ok=True)
        os.chmod(gconfig.config_path, stat.S_IRUSR | stat.S_IXUSR)
        try:
            detail = lockfile.write_pending_operation(gconfig, "install")
            assert detail is not None, "a failed arming must be reported, not just logged"
        finally:
            os.chmod(gconfig.config_path, 0o755)

    def test_absent_marker_stays_silent(self, gconfig) -> None:
        assert ui_model.check_pending_operation(gconfig, "install") == (None, None), (
            "no marker is the NORMAL case - no note noise"
        )


class TestPidReuseDefense:
    """#104: a recycled PID must not validate a foreign process as owner.
    The marker stores the owner's process START marker; readers verify it -
    a matching pid with a WRONG start marker is void. Where no start marker
    is obtainable (platform gap), pid+TTL remain the named residual."""

    def test_own_start_marker_is_obtainable_here(self) -> None:
        marker = lockfile.process_start_marker(os.getpid())
        assert marker, "on Linux/macOS the start marker must be readable for the own pid"

    def test_matching_pid_wrong_start_marker_is_void(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "install")
        path = gconfig.config_path / "pending-operation.json"
        data = json.loads(path.read_text())
        assert data.get("start"), "the writer must record its start marker"
        data["start"] = "recycled-pid-different-process"
        path.write_text(json.dumps(data))
        assert lockfile.read_pending_operation(gconfig) == (None, None), (
            "same pid but different process start: the owner died, the marker is void"
        )

    def test_matching_start_marker_stays_valid(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "install")
        marker, degraded = lockfile.read_pending_operation(gconfig)
        assert degraded is None and marker is not None and marker["action"] == "install"

    def test_marker_without_start_field_falls_back_to_pid(self, gconfig) -> None:
        # Older markers / platforms without a start marker: pid+TTL remain
        # the named residual - never a crash, never a false void.
        lockfile.write_pending_operation(gconfig, "install")
        path = gconfig.config_path / "pending-operation.json"
        data = json.loads(path.read_text())
        data.pop("start", None)
        path.write_text(json.dumps(data))
        marker, degraded = lockfile.read_pending_operation(gconfig)
        assert degraded is None and marker is not None
