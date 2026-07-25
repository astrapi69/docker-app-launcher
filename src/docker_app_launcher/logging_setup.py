"""Logging configuration for the launcher.

Three sinks, all best-effort (a logging-setup failure must never stop the
launcher from starting):

* ``stderr`` - always, so ``--debug`` runs stream live and CI captures output.
  stderr, not stdout: machine-readable CLI output (``--render-probe`` JSON,
  ``--status``) owns stdout and must never be interleaved with log lines.
* ``config.log_path`` (``<config_dir>/launcher.log``) - persistent, rotated.
* ``config.install_log_path`` (``<config_dir>/install.log``) - truncated each
  run, so it always holds the most recent run's activity.
* ``<cwd>/launcher-debug.log`` - only with ``debug=True``, truncated each run,
  so a fresh capture is easy to share.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.logging_setup")

# Uncaught exceptions (main thread + worker threads) log here (P1): without
# a hook they only ever reached stderr, which is invisible for a launcher
# started from a .desktop file or a frozen binary.
_uncaught_logger = logging.getLogger("docker_app_launcher.uncaught")

_hooks_installed = False

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DEBUG_LOG_NAME = "launcher-debug.log"


def setup_logging(config: LauncherConfig, *, debug: bool = False) -> None:
    """Attach stdout + file handlers to the root logger.

    Idempotent: our own handlers (marked via ``_dal_handler``) are removed
    before re-adding, so calling this twice (e.g. ``launch()`` after an app's
    own bootstrap) never duplicates log lines. Every file handler is added
    inside its own ``try`` so a read-only directory degrades to "fewer
    sinks", never a crash.
    """
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_dal_handler", False)]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG if debug else _parse_level(config.log_level))
    formatter = logging.Formatter(_FORMAT)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    _attach(root, stderr_handler)

    _add_rotating_file(root, formatter, config.log_path, config.log_max_size, config.log_backup_count)
    _add_truncating_file(root, formatter, config.install_log_path)

    if debug:
        _add_debug_file(root, formatter)

    _install_excepthooks()


def _install_excepthooks() -> None:
    """Log uncaught exceptions from the main thread and worker threads.

    Installed once per process. The previously installed hook is chained
    unless it is the interpreter default (whose only job — printing to
    stderr — would duplicate our stderr handler's output).
    """
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    previous_sys = sys.excepthook

    def sys_hook(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        _uncaught_logger.error("uncaught exception", exc_info=(exc_type, exc, tb))  # type: ignore[arg-type]
        if previous_sys is not sys.__excepthook__:
            previous_sys(exc_type, exc, tb)  # type: ignore[arg-type]

    sys.excepthook = sys_hook

    previous_threading = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        _uncaught_logger.error(
            "uncaught exception in thread %s",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[arg-type]
        )
        if previous_threading is not threading.__excepthook__:
            previous_threading(args)

    threading.excepthook = thread_hook


def _attach(root: logging.Logger, handler: logging.Handler) -> None:
    """Add ``handler`` marked as ours, so a later re-setup can remove it."""
    handler._dal_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def _parse_level(name: str) -> int:
    """Map a ``log_level`` string (``"INFO"``, ``"DEBUG"``, ...) to a level int.

    Falls back to ``INFO`` for an unknown name, so a typo never disables logging.
    """
    level = logging.getLevelName(str(name).upper())
    return level if isinstance(level, int) else logging.INFO


def _add_rotating_file(
    root: logging.Logger, formatter: logging.Formatter, path: Path, max_bytes: int, backup_count: int
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        handler.setFormatter(formatter)
        _attach(root, handler)
    except OSError as exc:
        logger.warning("could not open persistent log %s: %s", path, exc)


def _add_truncating_file(root: logging.Logger, formatter: logging.Formatter, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(path), mode="w", encoding="utf-8")
        handler.setFormatter(formatter)
        _attach(root, handler)
    except OSError as exc:
        logger.warning("could not open activity log %s: %s", path, exc)


def _add_debug_file(root: logging.Logger, formatter: logging.Formatter) -> None:
    """Attach the ``--debug`` CWD log; a missing-permission CWD is non-fatal."""
    try:
        debug_path = Path.cwd() / _DEBUG_LOG_NAME
        handler = logging.FileHandler(str(debug_path), mode="w", encoding="utf-8")
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        _attach(root, handler)
        logger.debug("Debug log: %s", debug_path)
    except OSError as exc:
        logger.warning("could not open %s: %s", _DEBUG_LOG_NAME, exc)
