"""Tests for the file-logging setup."""

from __future__ import annotations

import logging
import pathlib

import pytest

from docker_app_launcher import logging_setup
from docker_app_launcher.config import LauncherConfig


def _config(tmp_path) -> LauncherConfig:
    return LauncherConfig(app_name="Test App", config_dir=str(tmp_path / ".test-app")).resolve()


class TestSetupLogging:
    def test_creates_persistent_and_activity_logs(self, tmp_path) -> None:
        cfg = _config(tmp_path)
        logging_setup.setup_logging(cfg, debug=False)
        logging.getLogger("docker_app_launcher").info("hello")
        assert cfg.log_path.is_file()
        assert cfg.install_log_path.is_file()

    def test_no_debug_log_without_debug(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _config(tmp_path)
        logging_setup.setup_logging(cfg, debug=False)
        assert not (tmp_path / "launcher-debug.log").exists()

    def test_debug_writes_cwd_debug_log(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _config(tmp_path)
        logging_setup.setup_logging(cfg, debug=True)
        assert (tmp_path / "launcher-debug.log").is_file()
        assert logging.getLogger().level == logging.DEBUG

    def test_install_log_truncates_each_run(self, tmp_path) -> None:
        cfg = _config(tmp_path)
        cfg.install_log_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.install_log_path.write_text("STALE FROM LAST RUN\n", encoding="utf-8")
        logging_setup.setup_logging(cfg, debug=False)
        assert "STALE FROM LAST RUN" not in cfg.install_log_path.read_text(encoding="utf-8")

    def test_always_adds_stderr_handler(self, tmp_path) -> None:
        import sys

        cfg = _config(tmp_path)
        before = len(logging.getLogger().handlers)
        logging_setup.setup_logging(cfg, debug=False)
        after = logging.getLogger().handlers
        assert len(after) > before
        # stderr, never stdout: --render-probe JSON owns stdout.
        assert any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in after)

    def test_setup_twice_adds_no_duplicate_handlers(self, tmp_path) -> None:
        cfg = _config(tmp_path)
        logging_setup.setup_logging(cfg, debug=False)
        ours_first = [h for h in logging.getLogger().handlers if getattr(h, "_dal_handler", False)]
        logging_setup.setup_logging(cfg, debug=False)
        ours_second = [h for h in logging.getLogger().handlers if getattr(h, "_dal_handler", False)]
        assert len(ours_second) == len(ours_first)

    def test_resetup_keeps_foreign_handlers(self, tmp_path) -> None:
        foreign = logging.NullHandler()
        logging.getLogger().addHandler(foreign)
        try:
            logging_setup.setup_logging(_config(tmp_path), debug=False)
            assert foreign in logging.getLogger().handlers
        finally:
            logging.getLogger().removeHandler(foreign)

    def test_unwritable_config_dir_degrades(self, tmp_path, monkeypatch) -> None:
        # A log path whose parent cannot be created must not raise.
        cfg = _config(tmp_path)

        def boom(*a, **k):
            raise OSError("read-only")

        monkeypatch.setattr(pathlib.Path, "mkdir", boom)
        logging_setup.setup_logging(cfg, debug=False)  # must not raise


def test_debug_file_failure_is_nonfatal(tmp_path, monkeypatch) -> None:
    # An unwritable CWD must degrade (no debug sink), never crash the launcher.
    root = logging.getLogger("dal-test-debugfail")
    root.handlers.clear()
    missing = tmp_path / "does" / "not" / "exist"
    monkeypatch.setattr(pathlib.Path, "cwd", classmethod(lambda cls: missing))
    formatter = logging.Formatter("%(message)s")
    logging_setup._add_debug_file(root, formatter)  # must not raise
    assert not any(isinstance(handler, logging.FileHandler) for handler in root.handlers)


class TestExcepthooks:
    """P1: uncaught exceptions must reach the log, not just an invisible stderr."""

    # The deliberate crash also reaches pytest's own thread hook (we chain
    # foreign hooks by design), which turns it into this warning. On a loaded
    # CI runner pytest's unraisable-exception plugin can additionally trip over
    # tracemalloc during teardown ("partially initialized module 'tracemalloc'")
    # and escalate the SAME deliberate crash into a PytestUnraisableExceptionWarning
    # - noise from our own intentional thread crash, so it is ignored too. The
    # gc.collect() forces that collection to happen inside this filtered scope
    # instead of leaking into a later test.
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_thread_exception_is_logged(self, tmp_path, caplog) -> None:
        import gc
        import threading

        logging_setup.setup_logging(_config(tmp_path), debug=False)
        with caplog.at_level(logging.ERROR, logger="docker_app_launcher.uncaught"):
            worker = threading.Thread(target=lambda: 1 / 0, name="boom-thread")
            worker.start()
            worker.join()
        assert any("boom-thread" in r.message for r in caplog.records)
        assert any(r.exc_info and r.exc_info[0] is ZeroDivisionError for r in caplog.records)
        del worker
        gc.collect()

    def test_sys_excepthook_logs(self, tmp_path, caplog) -> None:
        import sys

        logging_setup.setup_logging(_config(tmp_path), debug=False)
        with caplog.at_level(logging.ERROR, logger="docker_app_launcher.uncaught"):
            try:
                raise RuntimeError("main thread crash")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
        assert any("uncaught exception" in r.message for r in caplog.records)

    def test_hooks_install_only_once(self, tmp_path) -> None:
        import sys
        import threading

        logging_setup.setup_logging(_config(tmp_path), debug=False)
        sys_hook, thread_hook = sys.excepthook, threading.excepthook
        logging_setup.setup_logging(_config(tmp_path), debug=False)
        assert sys.excepthook is sys_hook
        assert threading.excepthook is thread_hook
