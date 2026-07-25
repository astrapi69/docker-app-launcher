"""Which Compose frontend is usable HERE - plugin, legacy v1, or none (#48).

Verified device forensics: on a Docker 20.10 CLI without the compose
plugin, ``docker compose -p …`` fails with ``unknown shorthand flag: 'p'``
plus the full help dump (the CLI swallows the unknown word ``compose`` and
parses ``-p`` as a top-level flag). The detection ladder runs BEFORE any
build so that machine ever sees an actionable message instead.

Ladder, probed once per process and cached:

1. ``docker compose version``  -> ``"plugin"`` (Compose v2)
2. ``docker-compose --version`` -> legacy v1 - accepted only when it can
   actually parse the app's compose file (``docker-compose -f <file>
   config -q``), else ``"legacy_incompatible"``
3. neither                      -> ``"none"``
"""

from __future__ import annotations

import logging
import subprocess

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker.command_runner import _run

logger = logging.getLogger("docker_app_launcher.docker.compose_runtime")

# "plugin" | "legacy" | "legacy_incompatible" | "none"; None = not probed yet.
_COMPOSE_FRONTEND: str | None = None
_COMPOSE_DETAIL: str = ""


def reset_compose_cache() -> None:
    """Forget the probed frontend (re-checks and tests)."""
    global _COMPOSE_FRONTEND, _COMPOSE_DETAIL
    _COMPOSE_FRONTEND = None
    _COMPOSE_DETAIL = ""


def detect_compose(config: LauncherConfig) -> tuple[str, str]:
    """Return ``(frontend, detail)``; probes once, then serves the cache.

    ``frontend`` is ``"plugin"``, ``"legacy"``, ``"legacy_incompatible"``
    or ``"none"``. ``detail`` carries the probed version line (or the
    v1 validation error) for the log.
    """
    global _COMPOSE_FRONTEND, _COMPOSE_DETAIL
    if _COMPOSE_FRONTEND is not None:
        return _COMPOSE_FRONTEND, _COMPOSE_DETAIL
    _COMPOSE_FRONTEND, _COMPOSE_DETAIL = _probe(config)
    logger.info("compose frontend: %s (%s)", _COMPOSE_FRONTEND, _COMPOSE_DETAIL or "-")
    return _COMPOSE_FRONTEND, _COMPOSE_DETAIL


def _probe(config: LauncherConfig) -> tuple[str, str]:
    rc, out = _probe_cmd(["docker", "compose", "version"])
    if rc == 0:
        return "plugin", out
    rc, out = _probe_cmd(["docker-compose", "--version"])
    if rc != 0:
        return "none", ""
    version_line = out
    # v1 found - only accept it when it can parse THIS app's compose file
    # (profiles/conditions and other v2 spec features are not v1-clean).
    rc, err = _probe_cmd(["docker-compose", "-f", str(config.compose_path), "config", "-q"])
    if rc == 0:
        return "legacy", version_line
    return "legacy_incompatible", err or version_line


def _probe_cmd(cmd: list[str]) -> tuple[int | None, str]:
    """``(returncode, first output line)``; never raises."""
    try:
        result = _run(cmd, timeout=15.0, probe=True)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return None, "timeout"
    out = (result.stdout or result.stderr or "").strip().splitlines()
    return result.returncode, out[0] if out else ""


def compose_base_args(config: LauncherConfig) -> list[str]:
    """The command prefix every compose invocation is built from.

    ``["docker", "compose"]`` for the plugin, ``["docker-compose"]`` for
    accepted legacy v1 (both support ``-p``/``-f``). Callers must have
    passed :func:`compose_available` first; an unprobed or unusable state
    falls back to the plugin form (the build guard has already refused).
    """
    frontend, _ = detect_compose(config)
    if frontend == "legacy":
        return ["docker-compose"]
    return ["docker", "compose"]


def compose_available(config: LauncherConfig) -> tuple[bool, str]:
    """``(usable, frontend_or_verdict)`` - the build guard's question."""
    frontend, _ = detect_compose(config)
    return frontend in ("plugin", "legacy"), frontend
