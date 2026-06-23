"""Logging configuration for the launcher.

Three sinks, all best-effort (a logging-setup failure must never stop the
launcher from starting):

* ``stdout`` - always, so ``--debug`` runs stream live and CI captures output.
* ``config.log_path`` (``<config_dir>/launcher.log``) - persistent, rotated.
* ``config.install_log_path`` (``<config_dir>/install.log``) - truncated each
  run, so it always holds the most recent run's activity.
* ``<cwd>/launcher-debug.log`` - only with ``debug=True``, truncated each run,
  so a fresh capture is easy to share.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.logging_setup")

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DEBUG_LOG_NAME = "launcher-debug.log"


def setup_logging(config: LauncherConfig, *, debug: bool = False) -> None:
    """Attach stdout + file handlers to the root logger.

    Idempotent enough for a launcher process (called once at startup). Every
    file handler is added inside its own ``try`` so a read-only directory
    degrades to "fewer sinks", never a crash.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter(_FORMAT)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    _add_rotating_file(root, formatter, config.log_path)
    _add_truncating_file(root, formatter, config.install_log_path)

    if debug:
        _add_debug_file(root, formatter)


def _add_rotating_file(root: logging.Logger, formatter: logging.Formatter, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(str(path), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as exc:
        logger.warning("could not open persistent log %s: %s", path, exc)


def _add_truncating_file(root: logging.Logger, formatter: logging.Formatter, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(path), mode="w", encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as exc:
        logger.warning("could not open activity log %s: %s", path, exc)


def _add_debug_file(root: logging.Logger, formatter: logging.Formatter) -> None:
    """Attach the ``--debug`` CWD log; a missing-permission CWD is non-fatal."""
    try:
        debug_path = Path.cwd() / _DEBUG_LOG_NAME
        handler = logging.FileHandler(str(debug_path), mode="w", encoding="utf-8")
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        root.addHandler(handler)
        logger.debug("Debug log: %s", debug_path)
    except OSError as exc:
        logger.warning("could not open %s: %s", _DEBUG_LOG_NAME, exc)
