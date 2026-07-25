"""Native Docker API access via docker-py (#44).

Hybrid contract: INSPECTION goes through the Docker API when docker-py is
importable — typed exceptions with real errnos instead of scraping the
CLI's unversioned stderr text (the #27 root cause). The compose LIFECYCLE
stays on the CLI: Compose v2 is a Go plugin docker-py cannot replace.

Every helper degrades to ``"unavailable"`` instead of raising when
docker-py is missing (stripped frozen binary, exotic install), so the CLI
code paths remain the universal fallback.
"""

from __future__ import annotations

import contextlib
import errno
import logging
from typing import Any

from docker_app_launcher.docker.command_runner import docker_host_override

logger = logging.getLogger("docker_app_launcher.docker.py_client")

try:
    import docker as _dockerpy
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _dockerpy = None


def available() -> bool:
    """Whether docker-py could be imported."""
    return _dockerpy is not None


def get_client(endpoint: str | None = None, *, timeout: float = 10.0) -> Any:
    """A connected ``DockerClient`` for ``endpoint`` (or the environment).

    ``endpoint=None`` honors the #25 ``DOCKER_HOST`` context-fallback
    override first, then the process environment (``docker.from_env``).
    Raises ``RuntimeError`` when docker-py is unavailable and whatever
    docker-py raises when the daemon cannot be reached — callers that only
    need a health verdict should use :func:`ping` instead.
    """
    if _dockerpy is None:
        raise RuntimeError("docker-py is not installed")
    target = endpoint or docker_host_override()
    if target:
        return _dockerpy.DockerClient(base_url=target, timeout=timeout)
    return _dockerpy.from_env(timeout=timeout)


def ping(endpoint: str | None = None, *, timeout: float = 5.0) -> tuple[str, str]:
    """Classify daemon reachability: ``(status, detail)``.

    ``status`` is one of:

    * ``"ok"`` - the daemon answered the API ping
    * ``"permission"`` - the socket refused us with EACCES/EPERM (daemon UP,
      user not in the docker group - never report this as "not started", #27)
    * ``"down"`` - nothing answered (daemon stopped, socket missing, timeout)
    * ``"unavailable"`` - docker-py is not importable; caller must fall back
      to the CLI probe
    """
    if _dockerpy is None:
        return "unavailable", "docker-py not importable"
    client: Any = None
    try:
        client = get_client(endpoint, timeout=timeout)
        client.ping()
        return "ok", ""
    except Exception as exc:  # noqa: BLE001 - every failure maps to a verdict
        verdict = _classify_exception(exc)
        logger.debug("ping(%s) -> %s: %s", endpoint or "<env>", verdict, exc)
        return verdict, str(exc)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()


def _classify_exception(exc: BaseException, _depth: int = 0) -> str:
    """Walk the exception chain for the truthful signal.

    requests/urllib3 bury the original ``PermissionError`` inside nested
    ``args`` (``ProtocolError('Connection aborted.', PermissionError(13, …))``)
    rather than ``__cause__``, so both chains AND args are searched.
    """
    if _depth > 10:
        return "down"
    if isinstance(exc, PermissionError):
        return "permission"
    if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
        return "permission"
    for nested in _nested_exceptions(exc):
        if _classify_exception(nested, _depth + 1) == "permission":
            return "permission"
    return "down"


def _nested_exceptions(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    if exc.__cause__ is not None:
        out.append(exc.__cause__)
    if exc.__context__ is not None and exc.__context__ is not exc.__cause__:
        out.append(exc.__context__)
    out.extend(arg for arg in getattr(exc, "args", ()) if isinstance(arg, BaseException))
    return out
