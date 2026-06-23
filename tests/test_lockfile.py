"""Tests for the single-instance PID lockfile guard."""

from __future__ import annotations

import os

from docker_app_launcher import lockfile


class TestReadWriteClear:
    def test_write_then_read_roundtrip(self, tmp_path) -> None:
        path = tmp_path / "launcher.lock"
        lockfile.write_lock(path, pid=4242)
        assert lockfile.read_lock(path) == 4242

    def test_write_defaults_to_current_pid(self, tmp_path) -> None:
        path = tmp_path / "launcher.lock"
        lockfile.write_lock(path)
        assert lockfile.read_lock(path) == os.getpid()

    def test_read_absent_is_none(self, tmp_path) -> None:
        assert lockfile.read_lock(tmp_path / "nope.lock") is None

    def test_read_garbage_is_none(self, tmp_path) -> None:
        path = tmp_path / "launcher.lock"
        path.write_text("not-a-pid", encoding="utf-8")
        assert lockfile.read_lock(path) is None

    def test_write_creates_parent_dir(self, tmp_path) -> None:
        path = tmp_path / "deep" / "nested" / "launcher.lock"
        lockfile.write_lock(path, pid=1)
        assert path.is_file()

    def test_clear_is_idempotent(self, tmp_path) -> None:
        path = tmp_path / "launcher.lock"
        lockfile.write_lock(path, pid=1)
        lockfile.clear_lock(path)
        lockfile.clear_lock(path)  # second call must not raise
        assert not path.exists()


class TestLiveness:
    def test_current_process_is_alive(self) -> None:
        assert lockfile.pid_is_alive(os.getpid()) is True

    def test_unused_pid_is_dead(self) -> None:
        # PID 0 / a very high unused PID should not be a live process.
        assert lockfile.pid_is_alive(2_000_000_000) is False


class TestAnotherInstanceAlive:
    def test_no_lockfile_means_free(self, tmp_path) -> None:
        assert lockfile.another_instance_alive(tmp_path / "x.lock") is False

    def test_own_pid_does_not_count(self, tmp_path) -> None:
        path = tmp_path / "x.lock"
        lockfile.write_lock(path)  # our own PID
        assert lockfile.another_instance_alive(path) is False

    def test_dead_pid_means_free(self, tmp_path) -> None:
        path = tmp_path / "x.lock"
        lockfile.write_lock(path, pid=2_000_000_000)
        assert lockfile.another_instance_alive(path) is False

    def test_other_live_pid_means_taken(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "x.lock"
        lockfile.write_lock(path, pid=os.getpid() + 1)
        monkeypatch.setattr(lockfile, "pid_is_alive", lambda pid: True)
        assert lockfile.another_instance_alive(path) is True
