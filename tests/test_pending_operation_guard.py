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
        marker = lockfile.read_pending_operation(gconfig)
        assert marker is not None and marker["action"] == "install" and int(str(marker["pid"])) == os.getpid()
        lockfile.clear_pending_operation(gconfig)
        assert lockfile.read_pending_operation(gconfig) is None

    def test_dead_pid_voids_the_marker(self, gconfig) -> None:
        lockfile.write_pending_operation(gconfig, "update")
        path = gconfig.config_path / "pending-operation.json"
        data = json.loads(path.read_text())
        data["pid"] = 999999900  # certainly dead
        path.write_text(json.dumps(data))
        assert lockfile.read_pending_operation(gconfig) is None, (
            "a dead owner means the hung worker died with it - the marker is void"
        )

    def test_malformed_marker_is_void_not_a_crash(self, gconfig) -> None:
        path = gconfig.config_path / "pending-operation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken")
        assert lockfile.read_pending_operation(gconfig) is None


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
        assert lockfile.read_pending_operation(gconfig) is None, "expired marker is consumed"


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
