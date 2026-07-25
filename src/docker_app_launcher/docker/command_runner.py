"""Shared docker subprocess layer: runners, streaming, progress, DOCKER_HOST override.

Every docker command in the launcher goes through :func:`_run` /
:func:`_stream_command` here - central place for the #25 context-fallback
``DOCKER_HOST`` override, the Windows ``CREATE_NO_WINDOW`` kwargs, and the
build-progress parsing. No business decisions live here.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docker_app_launcher import i18n
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.subprocess_utils import subprocess_kwargs

logger = logging.getLogger("docker_app_launcher.docker.command_runner")


ProgressFn = Callable[[str], None]

OutputFn = Callable[[str], None]

# (percent, label). ``percent`` is 0-100 for determinate progress, or ``None``
# to request an indeterminate (animated) bar when the duration is unknown.
ProgressPctFn = Callable[["int | None", str], None]


def _t(config: LauncherConfig, key: str, **kwargs: Any) -> str:
    return i18n.t(key, config, **kwargs)


def _first_line(text: str) -> str:
    """The first non-empty line of ``text`` (docker's stderr headline)."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _progress(on_progress: ProgressPctFn | None, percent: int | None, label: str) -> None:
    """Report determinate (``percent`` 0-100) or indeterminate (``None``) progress."""
    if on_progress is not None:
        try:
            on_progress(percent, label)
        except Exception as exc:  # noqa: BLE001 - progress UI must never break an action
            logger.debug("progress callback failed: %s", exc)


class DockerBuildProgress:
    """Turn streamed ``docker build`` output into a 0-99 build percentage.

    BuildKit prints ``#<n> [stage x/y] ...`` lines. The step count is not known
    up front and differs per Dockerfile, so we DON'T hard-code it: we track the
    highest ``#<n>`` seen and divide by it (or by ``estimated_total`` when the
    app provides a hint, giving a smooth bar from the first line). ``CACHED`` /
    ``DONE`` lines also carry ``#<n>`` and so count too. ``report(percent, line)``
    is called per parsed line; the caller maps that percentage into its own band.
    """

    _STEP_RE = re.compile(r"#(\d+)\b")

    def __init__(self, report: Callable[[int, str], None], *, estimated_total: int = 0) -> None:
        self._report = report
        self._estimated_total = max(0, estimated_total)
        self._max_step = 0

    def parse_line(self, line: str) -> None:
        match = self._STEP_RE.search(line)
        if not match:
            return
        step = int(match.group(1))
        self._max_step = max(self._max_step, step)
        total = self._estimated_total or self._max_step
        if total > 0:
            # Cap at 99 so the bar never reaches 100% before the health check.
            self._report(min(step * 100 // total, 99), line.strip())


# Module-wide DOCKER_HOST override set by the #25 context fallback: once a
# working non-active context is found, every later docker command uses it.
_DOCKER_HOST_OVERRIDE: str | None = None


def _set_docker_host_override(endpoint: str) -> None:
    """Route every subsequent docker command through ``endpoint`` (#25)."""
    global _DOCKER_HOST_OVERRIDE
    _DOCKER_HOST_OVERRIDE = endpoint


def docker_host_override() -> str | None:
    """The endpoint the context fallback connected through, or ``None``."""
    return _DOCKER_HOST_OVERRIDE


def _reset_docker_host_override() -> None:
    """Forget a previous context-fallback endpoint (tests / re-checks)."""
    global _DOCKER_HOST_OVERRIDE
    _DOCKER_HOST_OVERRIDE = None


def _run(
    cmd: list[str],
    *,
    timeout: float = 15.0,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
    probe: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a docker command, capturing output. Logs the call for ``--debug``.

    ``extra_env`` entries (and the #25 ``DOCKER_HOST`` fallback override,
    when set) are layered over the inherited environment.

    Failures log at WARNING so they land in ``launcher.log`` at the default
    level. ``probe=True`` keeps them at DEBUG — status polls (``docker info``
    while waiting for the daemon) fail by design and must not spam the log.
    """
    logger.debug("exec: %s (cwd=%s, timeout=%ss)", " ".join(cmd), cwd, timeout)
    env: dict[str, str] | None = None
    if extra_env or _DOCKER_HOST_OVERRIDE:
        env = os.environ.copy()
        if _DOCKER_HOST_OVERRIDE:
            env["DOCKER_HOST"] = _DOCKER_HOST_OVERRIDE
        if extra_env:
            env.update(extra_env)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            **subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        # WARNING, not DEBUG: a swallowed timeout is exactly the kind of
        # "the launcher ate my error" report this log exists to answer.
        _log_failure(probe, "timeout after %ss: %s", timeout, " ".join(cmd))
        raise
    except FileNotFoundError:
        _log_failure(probe, "binary not found: %s", cmd[0])
        raise
    if result.returncode != 0:
        _log_failure(
            probe,
            "command failed (exit=%s): %s stderr=%r",
            result.returncode,
            " ".join(cmd),
            (result.stderr or "")[-1500:],
        )
    else:
        logger.debug(
            "exit=%s stdout=%r stderr=%r",
            result.returncode,
            (result.stdout or "")[-1500:],
            (result.stderr or "")[-1500:],
        )
    return result


def _log_failure(probe: bool, msg: str, *args: object) -> None:
    """A failed command: WARNING normally, DEBUG for expected-to-fail probes."""
    logger.log(logging.DEBUG if probe else logging.WARNING, msg, *args)


def _notify(on_step: ProgressFn | None, label: str) -> None:
    if on_step is not None:
        try:
            on_step(label)
        except Exception as exc:  # noqa: BLE001 - progress UI must never break an action
            logger.debug("progress callback failed: %s", exc)


def _stream_command(
    cmd: list[str],
    *,
    on_output: OutputFn | None = None,
    timeout: float,
    cwd: Path | None = None,
    tail_lines: int = 15,
    keep: int = 400,
) -> tuple[int, str]:
    """Run ``cmd``, streaming combined stdout+stderr line-by-line to
    ``on_output`` as each line arrives. Returns ``(returncode, tail)`` where
    ``tail`` is the last ``tail_lines`` lines (for an error message).

    Unlike :func:`_run`, this surfaces progress live - a Docker build prints
    for minutes and the user must see it move. A watchdog timer kills the
    process after ``timeout`` and the call then raises
    :class:`subprocess.TimeoutExpired`, matching :func:`_run`'s contract.
    """
    logger.debug("stream: %s (timeout=%ss)", " ".join(cmd), timeout)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(cwd) if cwd else None,
        **subprocess_kwargs(),
    )
    lines: list[str] = []
    killed = {"v": False}

    def _kill() -> None:
        killed["v"] = True
        proc.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if len(lines) > keep:
                lines.pop(0)
            if on_output is not None:
                try:
                    on_output(line)
                except Exception as exc:  # noqa: BLE001 - output UI must never break the build
                    logger.debug("output callback failed: %s", exc)
        proc.wait()
    finally:
        timer.cancel()
    if killed["v"]:
        logger.warning("stream timeout after %ss: %s", timeout, " ".join(cmd))
        raise subprocess.TimeoutExpired(cmd, timeout)
    tail = "\n".join(lines[-tail_lines:])
    if proc.returncode != 0:
        logger.warning("stream failed (exit=%s): %s tail=%r", proc.returncode, " ".join(cmd), tail)
    return proc.returncode, tail


def _step_label(config: LauncherConfig, label: str, ok: bool, detail: str) -> str:
    """Format one verbose step line: ``<label>... ✓`` or
    ``<label>... ✗ <Error>: <detail>``."""
    if ok:
        return f"{label}... ✓"
    return f"{label}... ✗ {_t(config, 'error_word')}: {detail}"


def _docker_op(cmd: list[str], *, timeout: float = 60.0) -> tuple[bool, str]:
    """Run ONE docker step. Returns ``(ok, detail)`` - ``detail`` is the
    trimmed last stderr line on failure. Never raises."""
    try:
        result = _run(cmd, timeout=timeout)
    except FileNotFoundError:
        return False, "docker not found"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return False, stderr.splitlines()[-1] if stderr else "unknown error"
    return True, ""
