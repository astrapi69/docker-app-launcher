"""PID-lockfile based single-instance guard.

Windows has no portable POSIX file-locking story, so we use the simplest
thing that works across platforms: write our PID to a file; on launch,
read it and check whether that PID is still alive. If so, another
instance is already running and the new one should bow out.

Pure and path-driven (the lockfile path comes from
:attr:`~docker_app_launcher.config.LauncherConfig.lock_path`), so it is
fully unit-testable without a real second process.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.lockfile")


def read_lock(path: Path) -> int | None:
    """Return the PID recorded in the lockfile, or ``None`` if absent/invalid."""
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        # Deliberate, VISIBLE open (#103): failing closed on an unreadable
        # lock would refuse every start over a full/read-only disk. The
        # console+logfile note is the visibility channel here - no window
        # exists yet at this point.
        logger.warning(
            "single-instance lock not readable (%s) - continuing WITHOUT the single-instance protection", exc
        )
        return None
    if not content.isdigit():
        return None
    return int(content)


def write_lock(path: Path, pid: int | None = None) -> None:
    """Write ``pid`` (default: this process) to the lockfile."""
    pid = pid if pid is not None else os.getpid()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def clear_lock(path: Path) -> None:
    """Remove the lockfile, ignoring a missing file or a removal error."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check (Windows ``tasklist``; POSIX signal 0)."""
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    return _pid_alive_posix(pid)


def _pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user. For a per-user
        # lockfile under the config dir we should never hit this; treat as
        # alive to err on the side of "do not start a second instance".
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    import subprocess

    from docker_app_launcher.subprocess_utils import subprocess_kwargs

    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5.0,
            **subprocess_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # If we cannot check, prefer "alive" so we never silently clobber a
        # running launcher's lockfile.
        return True
    # ``result.stdout`` can be None on a Windows locale edge case; guard so
    # the ``in`` operator below never raises TypeError on NoneType.
    output = result.stdout or ""
    return str(pid) in output


def another_instance_alive(path: Path) -> bool:
    """True if the lockfile points at a different, still-running PID."""
    pid = read_lock(path)
    if pid is None:
        return False
    if pid == os.getpid():
        return False
    return pid_is_alive(pid)


# --- focus handshake (#31) --------------------------------------------------
# A refused second launch should not just print a notice - it should bring
# the FIRST window to the foreground. The channel is a marker file next to
# the lockfile: the second instance touches it, the running window polls and
# consumes it. File-based on purpose: portable (no socket permissions story
# on Windows), unit-testable without a second process, and self-cleaning.


def focus_request_path(lock_path: Path) -> Path:
    """The focus-request marker belonging to ``lock_path``."""
    return lock_path.with_name(lock_path.name + ".focus")


def request_focus(lock_path: Path) -> None:
    """Second instance: ask the running window to come to the foreground."""
    try:
        marker = focus_request_path(lock_path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        # Focus is best-effort: the notice on stdout still tells the user.
        logger.debug("could not write focus request: %s", exc)


def consume_focus_request(lock_path: Path) -> bool:
    """Running window: True once per pending focus request (marker removed)."""
    marker = focus_request_path(lock_path)
    if not marker.is_file():
        return False
    with contextlib.suppress(OSError):
        marker.unlink()
        return True
    return False


# --- pending-operation marker (#102) -----------------------------------------
# The concurrency guard's cross-process carrier: the GUI writes it when a
# cancel goes unresponsive, clears it on the late result. PID-bound on
# purpose: the hung worker is a THREAD of the GUI process, so a dead owner
# pid means the hung operation died with it - the marker voids itself, a
# crashed GUI never blocks forever, and "restart clears it" is mechanically
# true rather than a promise.

_PENDING_FILE = "pending-operation.json"


def _pending_path(config: LauncherConfig) -> Path:
    return config.config_path / _PENDING_FILE


def write_pending_operation(config: LauncherConfig, action: str) -> str | None:
    """Arm the guard; returns None on success, else the failure detail.

    DELIBERATE fail-open (#103, the named exception to contract point 3):
    the guard must not depend on a writable disk - but the CALLER must
    surface the returned detail visibly, because a silently missing
    protection is the worst case.
    """
    import json as _json
    import time as _time

    try:
        path = _pending_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps({"action": action, "at": _time.time(), "pid": os.getpid()}),
            encoding="utf-8",
        )
        return None
    except OSError as exc:
        logger.warning("could not write pending-operation marker: %s", exc)
        return str(exc)


def clear_pending_operation(config: LauncherConfig) -> None:
    import contextlib as _contextlib

    with _contextlib.suppress(OSError):
        _pending_path(config).unlink(missing_ok=True)


def read_pending_operation(config: LauncherConfig) -> tuple[dict[str, object] | None, str | None]:
    """``(marker, degraded_detail)``.

    ``(None, None)``   - no marker, or a VALIDLY void one (dead owner):
                         the normal silent cases.
    ``(marker, None)`` - an armed guard.
    ``(None, detail)`` - the guard CANNOT work: file present but unreadable,
                         or unparsable content (consumed so the note does not
                         repeat forever). DELIBERATE fail-open (#103): the
                         caller proceeds but must surface the detail - a
                         silently missing protection is the worst case.
    """
    import contextlib as _contextlib
    import json as _json

    path = _pending_path(config)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, str(exc)
    try:
        data = _json.loads(raw)
    except ValueError as exc:
        with _contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return None, f"unparsable marker content ({exc})"
    if not isinstance(data, dict):
        with _contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return None, "unparsable marker content (not an object)"
    try:
        pid = int(str(data.get("pid", 0)))
    except ValueError:
        with _contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return None, "unparsable marker content (bad pid)"
    if pid <= 0 or not pid_is_alive(pid):
        return None, None  # dead owner: validly void, the marker self-voids
    return data, None
