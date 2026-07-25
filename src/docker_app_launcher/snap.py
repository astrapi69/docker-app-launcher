"""Detect a Snap-confined launcher and surface the limitation (G7, #63).

Under Canonical's strict Snap confinement the engine version is fine, but the
sandbox breaks the launcher's path assumptions: ``HOME`` is remapped to
``$SNAP_USER_DATA``, compose files in hidden ``$HOME`` subdirs fail with
permission denied (canonical/docker-snap#334), and bind mounts to paths
outside the allowed locations silently succeed-but-do-nothing
(canonical/docker-snap#189). The launcher cannot fix the sandbox, but it must
not fail silently: it detects the confinement and logs a clear, documented
warning so the behaviour is explained rather than mysterious.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("docker_app_launcher.snap")

_WARNING = (
    "Running under Snap confinement (SNAP=%s). Paths outside the snap-writable "
    "area may be inaccessible: a compose file or build context under your real "
    "home, /mnt, or /media can fail to read, and bind mounts to such paths can "
    "silently do nothing. Prefer install_dir under $SNAP_USER_DATA, or install "
    "the launcher outside Snap. See docs/environment-matrix.md (G7)."
)


def is_snap_confined() -> bool:
    """Whether the process runs inside a Snap sandbox.

    Snap sets ``SNAP`` (the read-only squashfs mount) and ``SNAP_NAME`` for
    every confined process; either being present is the reliable signal.
    """
    return bool(os.environ.get("SNAP") or os.environ.get("SNAP_NAME"))


def log_confinement_warning() -> bool:
    """Log the confinement warning once at startup; return whether it fired.

    Best-effort and never raises - a logging failure must not stop the
    launcher (mirrors the logging-setup posture).
    """
    if not is_snap_confined():
        return False
    logger.warning(_WARNING, os.environ.get("SNAP", "?"))
    return True
