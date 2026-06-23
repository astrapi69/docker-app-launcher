"""Shared subprocess helpers.

The single reason this module exists: on Windows every :func:`subprocess.run`
and :class:`subprocess.Popen` call spawns a visible console window unless told
otherwise. During an install the launcher fires dozens of ``docker`` commands
back to back, so without the ``CREATE_NO_WINDOW`` flag the user sees a swarm of
CMD windows flickering open and shut - which looks exactly like malware.

Every subprocess call in the package routes its keyword arguments through
:func:`subprocess_kwargs` so the flag is applied consistently. ``actions._run``
imports no ``tkinter`` and this module keeps that property.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def subprocess_kwargs() -> dict[str, Any]:
    """Return kwargs that suppress a console window on Windows.

    On Windows this is ``{"creationflags": subprocess.CREATE_NO_WINDOW}``; on
    every other platform it is an empty dict (the flag does not exist there, so
    referencing it is guarded behind the platform check). Splat the result into
    any ``subprocess.run`` / ``subprocess.Popen`` call::

        subprocess.run(cmd, capture_output=True, **subprocess_kwargs())
    """
    if sys.platform == "win32":
        # ``CREATE_NO_WINDOW`` only exists on Windows; only reference it here.
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
