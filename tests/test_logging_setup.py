"""Tests for the file-logging setup."""

from __future__ import annotations

import logging
import pathlib

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

    def test_always_adds_stdout_handler(self, tmp_path) -> None:
        cfg = _config(tmp_path)
        before = len(logging.getLogger().handlers)
        logging_setup.setup_logging(cfg, debug=False)
        after = logging.getLogger().handlers
        assert len(after) > before
        assert any(isinstance(h, logging.StreamHandler) for h in after)

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
