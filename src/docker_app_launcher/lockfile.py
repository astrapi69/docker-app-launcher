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

logger = logging.getLogger("docker_app_launcher.lockfile")


def read_lock(path: Path) -> int | None:
    """Return the PID recorded in the lockfile, or ``None`` if absent/invalid."""
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
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

    _CREATE_NO_WINDOW = 0x08000000
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5.0,
            creationflags=_CREATE_NO_WINDOW,
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
