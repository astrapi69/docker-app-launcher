"""Is Docker usable HERE - and if not, why exactly.

Owns the detection chain the no-docker screen runs on: ``check_docker`` /
``check_docker_detailed``, the #25 context sweep, the errno-based socket
probe (#27 - never trust CLI error text), starting the daemon/Desktop,
waiting out the Desktop VM boot (#28), and the Linux docker-group
self-repair via pkexec (#27).
"""

from __future__ import annotations

import contextlib
import errno
import getpass
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import py_client
from docker_app_launcher.docker.command_runner import (
    ProgressFn,
    ProgressPctFn,
    _first_line,
    _notify,
    _reset_docker_host_override,
    _run,
    _set_docker_host_override,
    _t,
)
from docker_app_launcher.subprocess_utils import subprocess_kwargs

logger = logging.getLogger("docker_app_launcher.docker.detection")

# Daemon up, socket refused us: the ONE case that must never read as
# "not started" (#27).
_PERMISSION_MESSAGE = (
    "Docker is running, but your user lacks permission (not in the 'docker' group). "
    "Run 'sudo usermod -aG docker $USER' AND then log out and back in (or reboot) - "
    "the group change only becomes active in a new login session."
)


def _api_ping(endpoint: str | None = None) -> tuple[str, str]:
    """Indirection over :func:`py_client.ping` so tests can isolate the API."""
    return py_client.ping(endpoint)


def docker_installed() -> tuple[bool, str]:
    """Return ``(installed, message)``. True if the ``docker`` binary exists.

    Distinct from :func:`check_docker`: this only checks the CLI is present
    (``docker --version``), not whether the daemon is running.
    """
    try:
        result = _run(["docker", "--version"], timeout=10.0)
    except FileNotFoundError:
        return False, "Docker is not installed (docker not in PATH)."
    except subprocess.TimeoutExpired:
        return False, "Docker is not responding."
    if result.returncode != 0:
        return False, (result.stderr or "").strip() or "docker --version failed."
    return True, (result.stdout or "").strip() or "Docker is installed."


def _docker_contexts() -> list[tuple[str, str, bool]]:
    """``[(name, endpoint, is_active)]`` from ``docker context ls``.

    Degrades to ``[]`` on any failure (old CLI without context support,
    missing binary, timeout) - the caller then behaves exactly as before
    the #25 sweep existed.
    """
    try:
        result = _run(
            ["docker", "context", "ls", "--format", "{{.Name}}\t{{.DockerEndpoint}}\t{{.Current}}"],
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    contexts: list[tuple[str, str, bool]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            contexts.append((parts[0], parts[1], len(parts) > 2 and parts[2].strip().lower() == "true"))
    return contexts


def _sweep_other_contexts(
    config: LauncherConfig | None = None, on_step: ProgressFn | None = None
) -> tuple[str, str] | None:
    """Probe every non-active context; on a hit, connect through it.

    Returns ``(context_name, endpoint)`` of the first context whose
    ``docker info`` succeeds and sets the module-wide ``DOCKER_HOST``
    override so every later docker command uses that endpoint (#25 -
    the active context points at a dead socket while Docker actually
    runs under e.g. ``desktop-linux`` or a rootless context).

    With ``config`` + ``on_step`` each probed endpoint is reported to the
    caller ("Checking Docker context 'x' (…)"), so a multi-second sweep is
    visible in the window log instead of looking frozen (#30).
    """
    for name, endpoint, is_active in _docker_contexts():
        if is_active:
            continue
        if on_step is not None and config is not None:
            _notify(on_step, _t(config, "checking_context", context=name, endpoint=endpoint))
        if _probe_endpoint(endpoint):
            logger.info("docker reachable via context %r (%s); overriding DOCKER_HOST", name, endpoint)
            _set_docker_host_override(endpoint)
            return name, endpoint
    return None


def _probe_endpoint(endpoint: str) -> bool:
    """Whether a daemon answers on ``endpoint`` - native API first, CLI fallback."""
    status, _ = _api_ping(endpoint)
    if status == "unavailable":
        rc, _stderr = _docker_info_rc(extra_env={"DOCKER_HOST": endpoint})
        return rc == 0
    return status == "ok"


def _active_context() -> tuple[str, str]:
    """Best-effort ``(name, endpoint)`` of the active context for diagnostics."""
    for name, endpoint, is_active in _docker_contexts():
        if is_active:
            return name, endpoint
    return "default", os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")


def _probe_unix_socket(endpoint: str) -> str | None:
    """Classify the ACTUAL socket signal: ``"permission"`` | ``"down"`` | ``None``.

    The docker CLI's error text is neither versioned nor guaranteed - on a
    real device it reported the generic connect message for an EACCES socket,
    which routed the user into the daemon-down flow (#27 reopened). A direct
    connect on the active unix endpoint gives the truthful errno instead:
    EACCES/EPERM -> missing docker-group membership; ECONNREFUSED/ENOENT ->
    the daemon really is down. Non-unix endpoints (tcp, npipe) return None
    and leave the caller's existing logic untouched.
    """
    if not endpoint.startswith("unix://"):
        return None
    path = endpoint[len("unix://") :]
    if not os.path.exists(path):
        return "down"
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(2.0)
    try:
        sock.connect(path)
        return None  # connectable: neither down nor a permission problem
    except PermissionError:
        return "permission"
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return "permission"
        if exc.errno in (errno.ECONNREFUSED, errno.ENOENT):
            return "down"
        return None
    finally:
        sock.close()


def _daemon_status() -> tuple[str, str]:
    """Combined native+CLI verdict for the ACTIVE endpoint.

    ``('ok'|'permission'|'down'|'no_response'|'no_cli', detail)``. The
    docker-py ping is authoritative for ``ok`` and ``permission`` (typed
    exceptions, #27); everything else falls through to the CLI probe, which
    still owns not-installed detection (exit 127) and the timeout signal.
    """
    api_status, api_detail = _api_ping()
    if api_status in ("ok", "permission"):
        return api_status, api_detail
    rc, stderr = _docker_info_rc()
    if rc == 0:
        return "ok", ""
    if rc == 127:
        return "no_cli", stderr
    if rc is None:
        return "no_response", stderr
    if "permission denied" in stderr.lower() or _probe_unix_socket(_active_context()[1]) == "permission":
        return "permission", stderr
    return "down", stderr


def check_docker() -> tuple[bool, str]:
    """Return ``(running, message)``. True only when the daemon is reachable.

    When the ACTIVE context is unreachable the other contexts are probed
    and, on a hit, used for every later docker command (#25).
    """
    _reset_docker_host_override()
    status, _detail = _daemon_status()
    if status == "ok":
        return True, "Docker is running."
    if status == "no_cli":
        return False, "Docker is not installed (docker not in PATH)."
    if status == "no_response":
        return False, "Docker is not responding (Docker Desktop may still be starting)."
    if status == "permission":
        # The daemon is (very likely) up - the socket exists but refused
        # us. Reporting "not started" here sends the user chasing
        # systemctl for a service that already runs (#27).
        return False, _PERMISSION_MESSAGE
    fallback = _sweep_other_contexts()
    if fallback is not None:
        return True, f"Docker is running (via context '{fallback[0]}')."
    return False, "Docker is not started."


_DOCKER_INSTALL_URLS = {
    "Windows": "https://docs.docker.com/desktop/install/windows-install/",
    "Linux": "https://docs.docker.com/engine/install/",
    "Darwin": "https://docs.docker.com/desktop/install/mac-install/",
}


def _docker_info_rc(extra_env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """Run ``docker info``: ``(returncode, stderr)``; ``returncode=None`` on timeout.

    ``extra_env`` lets the #25 context sweep probe a specific endpoint via
    ``DOCKER_HOST`` without touching the process environment.
    """
    try:
        result = _run(["docker", "info"], timeout=10.0, extra_env=extra_env, probe=True)
    except FileNotFoundError:
        return 127, "docker not found"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    return result.returncode, (result.stderr or "")


def check_docker_detailed(config: LauncherConfig, *, on_step: ProgressFn | None = None) -> dict[str, Any]:
    """Platform-specific Docker diagnostics for the "no Docker" screen.

    Returns a dict with ``platform`` (Linux/Windows/Darwin), ``installed``,
    ``running`` (bools), ``detail`` (a localized message), ``command`` (a
    copy-pasteable shell hint, or ``""``), ``install_url``, and ``can_start``
    (whether a Start-Docker button applies). Never raises - every probe is
    guarded, so a weird host degrades to "not installed".
    """
    system = platform.system()
    out: dict[str, Any] = {
        "platform": system,
        "installed": False,
        "running": False,
        "detail": "",
        "command": "",
        "install_url": config.docker_install_url or _DOCKER_INSTALL_URLS.get(system, _DOCKER_INSTALL_URLS["Linux"]),
        "can_start": False,
        "can_fix_permission": False,
    }
    has_cli = shutil.which("docker") is not None

    if system == "Linux":
        if not has_cli:
            out["detail"] = _t(config, "docker_not_installed")
            out["command"] = "sudo apt install docker.io docker-compose-plugin"
            return out
        out["installed"] = True
        status, detail = _daemon_status()
        if status == "ok":
            out["running"] = True
            out["detail"] = _t(config, "docker_running")
        elif status == "no_response":
            out["detail"] = _t(config, "docker_no_response")
            out["command"] = "sudo systemctl restart docker"
        elif status == "permission":
            out["detail"] = _t(config, "docker_no_permission")
            out["command"] = "sudo usermod -aG docker $USER"
            out["can_fix_permission"] = True
        else:
            fallback = _sweep_other_contexts(config, on_step)
            if fallback is not None:
                out["running"] = True
                out["detail"] = _t(config, "docker_running_other_context", context=fallback[0])
                return out
            name, endpoint = _active_context()
            out["detail"] = _t(
                config,
                "docker_not_running_detail",
                context=name,
                endpoint=endpoint,
                error=_first_line(detail) or "no response",
            )
            out["command"] = "sudo systemctl start docker"
            out["can_start"] = True
        return out

    # Windows / macOS: Docker Desktop.
    if system == "Windows":
        default_path = os.path.expandvars(r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe")
    else:  # Darwin and any other -> treat as Desktop-style
        default_path = "/Applications/Docker.app"
    desktop_path = config.docker_desktop_path or default_path

    if not has_cli:
        if os.path.exists(desktop_path):
            out["installed"] = True
            out["detail"] = _t(config, "docker_no_path")
            out["can_start"] = True
        else:
            out["detail"] = _t(config, "docker_not_installed")
        return out
    out["installed"] = True
    status, detail = _daemon_status()
    if status == "ok":
        out["running"] = True
        out["detail"] = _t(config, "docker_running")
    elif status == "no_response":
        out["detail"] = _t(config, "docker_no_response")
    else:
        # "permission" has no self-repair on Desktop platforms - treated as
        # not-running with the start offer, exactly as before.
        fallback = _sweep_other_contexts(config, on_step)
        if fallback is not None:
            out["running"] = True
            out["detail"] = _t(config, "docker_running_other_context", context=fallback[0])
            return out
        name, endpoint = _active_context()
        out["detail"] = _t(
            config,
            "docker_not_running_detail",
            context=name,
            endpoint=endpoint,
            error=_first_line(detail) or "no response",
        )
        out["can_start"] = True
    return out


def start_docker_daemon() -> tuple[bool, str]:
    """Linux: try to start the Docker daemon (systemctl, then a graphical pkexec)."""
    for cmd in (["systemctl", "start", "docker"], ["pkexec", "systemctl", "start", "docker"]):
        try:
            result = _run(cmd, timeout=30.0)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True, "Docker daemon started."
    return False, "Could not start the Docker daemon."


def start_docker_desktop(config: LauncherConfig) -> tuple[bool, str]:
    """Windows / macOS: launch Docker Desktop (no wait). Never raises."""
    system = platform.system()
    if system == "Windows":
        path = config.docker_desktop_path or os.path.expandvars(r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe")
        if os.path.exists(path):
            with contextlib.suppress(OSError):
                subprocess.Popen([path], **subprocess_kwargs())
                return True, "Docker Desktop starting..."
    elif system == "Darwin":
        app = config.docker_desktop_path or "/Applications/Docker.app"
        if os.path.exists(app):
            with contextlib.suppress(OSError):
                subprocess.Popen(["open", app], **subprocess_kwargs())
                return True, "Docker Desktop starting..."
    return False, "Docker Desktop not found."


def wait_for_docker(
    config: LauncherConfig,
    *,
    timeout: float = 90.0,
    interval: float = 2.0,
    on_progress: ProgressPctFn | None = None,
) -> tuple[bool, str]:
    """Poll :func:`check_docker` until the daemon answers or ``timeout`` hits.

    Docker Desktop boots a VM after ``open -a Docker`` - seconds to minutes -
    so rechecking immediately after a successful start would report "not
    started" again although the start worked (#28). ``on_progress`` receives
    indeterminate updates (``percent=None``) with a localized waiting label.
    """
    deadline = time.monotonic() + timeout
    while True:
        ok, message = check_docker()
        if ok:
            return True, _t(config, "docker_running")
        if time.monotonic() >= deadline:
            return False, message
        if on_progress is not None:
            with contextlib.suppress(Exception):
                on_progress(None, _t(config, "docker_desktop_waiting"))
        time.sleep(interval)


def add_user_to_docker_group(config: LauncherConfig) -> tuple[bool, str]:
    """Linux self-repair for the socket-permission case (#27): add the current
    user to the ``docker`` group via ``pkexec usermod`` and VERIFY it stuck.

    The caller must have confirmed the security implication first (docker
    group membership is effectively root). Success is verified against
    ``getent group docker`` and the success message still demands a re-login:
    the group change only becomes active in a NEW login session, so this
    function must never suggest Docker is usable already.
    """
    if platform.system() != "Linux":
        return False, _t(config, "docker_group_failed", error="Linux only")
    user = getpass.getuser()
    try:
        result = _run(["pkexec", "usermod", "-aG", "docker", user], timeout=120.0)
    except FileNotFoundError:
        return False, _t(config, "docker_group_failed", error="pkexec not found")
    except subprocess.TimeoutExpired:
        return False, _t(config, "docker_group_failed", error="timed out")
    if result.returncode in (126, 127):  # polkit dialog dismissed / not authorized
        return False, _t(config, "docker_group_cancelled")
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        error = stderr.splitlines()[-1] if stderr else f"exit {result.returncode}"
        return False, _t(config, "docker_group_failed", error=error)
    try:
        verify = _run(["getent", "group", "docker"], timeout=15.0)
        members_field = (verify.stdout or "").strip().split(":")[-1] if verify.returncode == 0 else ""
        members = [m.strip() for m in members_field.split(",") if m.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        members = []
    if user not in members:
        return False, _t(config, "docker_group_failed", error="verification failed (user not in group)")
    return True, _t(config, "docker_group_added")
