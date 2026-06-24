"""Business logic - the single layer the GUI and CLI both call.

Every launcher operation lives here as an isolated function. ``gui.py`` and
``__main__.py`` call ONLY these functions; this module imports NO tkinter, so
every action is unit-testable with pytest without a display.

Contract for every action:

- Takes a :class:`~docker_app_launcher.config.LauncherConfig` (plus plain
  parameters) - nothing is hard-coded; the app name, container name, port,
  health endpoint and timeouts all come from the config.
- Returns ``(success: bool, message: str)`` (a few return richer tuples where
  documented, e.g. :func:`find_free_port`).
- VERIFIES its result rather than blindly reporting success (uninstall
  re-lists the containers; install runs a health check).

Long-running actions (:func:`install`, :func:`start`) accept an optional
``on_step(label)`` progress callback, and stream the Docker build output
line-by-line through ``on_output(line)``. Both are plain callables; the GUI
passes ones that marshal onto the Tk thread, but the action neither knows nor
cares.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docker_app_launcher import __version__, i18n
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.subprocess_utils import subprocess_kwargs

logger = logging.getLogger("docker_app_launcher.actions")

MIN_PORT = 1024
MAX_PORT = 65535
# Internal (container) ports are not published on the host, so they are not
# bound by the 1024 floor a host-published port needs (e.g. nginx :80).
MIN_INTERNAL_PORT = 1

ProgressFn = Callable[[str], None]
OutputFn = Callable[[str], None]


def _t(config: LauncherConfig, key: str, **kwargs: Any) -> str:
    return i18n.t(key, config, **kwargs)


# --- low-level command runners --------------------------------------------


def _run(cmd: list[str], *, timeout: float = 15.0, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a docker command, capturing output. Logs the call for ``--debug``."""
    logger.debug("exec: %s (cwd=%s, timeout=%ss)", " ".join(cmd), cwd, timeout)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        **subprocess_kwargs(),
    )
    logger.debug(
        "exit=%s stdout=%r stderr=%r",
        result.returncode,
        (result.stdout or "")[-1500:],
        (result.stderr or "")[-1500:],
    )
    return result


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
        raise subprocess.TimeoutExpired(cmd, timeout)
    return proc.returncode, "\n".join(lines[-tail_lines:])


# --- Docker + state -------------------------------------------------------


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


def check_docker() -> tuple[bool, str]:
    """Return ``(running, message)``. True only when the daemon is reachable."""
    try:
        result = _run(["docker", "info"], timeout=10.0)
    except FileNotFoundError:
        return False, "Docker is not installed (docker not in PATH)."
    except subprocess.TimeoutExpired:
        return False, "Docker is not responding (Docker Desktop may still be starting)."
    if result.returncode != 0:
        return False, "Docker is not started."
    return True, "Docker is running."


def _name_filter_args(config: LauncherConfig) -> list[str]:
    args: list[str] = []
    for flt in config.name_filters():
        args += ["--filter", f"name={flt}"]
    return args


def _project_container_ids(config: LauncherConfig, *, running_only: bool) -> list[str]:
    cmd = ["docker", "ps", "-q"] if running_only else ["docker", "ps", "-aq"]
    cmd += _name_filter_args(config)
    try:
        result = _run(cmd, timeout=15.0)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [cid for cid in (result.stdout or "").strip().splitlines() if cid]


def get_state(config: LauncherConfig) -> str:
    """Return ``'no_docker' | 'not_installed' | 'running' | 'stopped'``."""
    docker_ok, _ = check_docker()
    if not docker_ok:
        return "no_docker"
    if _project_container_ids(config, running_only=True):
        return "running"
    if _project_container_ids(config, running_only=False):
        return "stopped"
    return "not_installed"


# --- Ports ----------------------------------------------------------------


def _validate_port(port: object) -> tuple[bool, str]:
    if not isinstance(port, int) or isinstance(port, bool) or not (MIN_PORT <= port <= MAX_PORT):
        return False, f"Port must be between {MIN_PORT} and {MAX_PORT}."
    return True, ""


def _validate_internal_port(port: object) -> tuple[bool, str]:
    """Validate an internal (container) port. Allows the full 1-65535 range."""
    if not isinstance(port, int) or isinstance(port, bool) or not (MIN_INTERNAL_PORT <= port <= MAX_PORT):
        return False, f"Internal port must be between {MIN_INTERNAL_PORT} and {MAX_PORT}."
    return True, ""


def check_port(port: int, *, host: str = "") -> tuple[bool, str]:
    """Return ``(free, message)``. Validates the range, then probes by BIND.

    Bind (not connect) is the correct check for "can docker publish this
    port": Docker publishes by binding, so we bind the same way. On Windows
    ``SO_EXCLUSIVEADDRUSE`` is set so an occupied port is detected reliably.
    """
    valid, reason = _validate_port(port)
    if not valid:
        return False, reason
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows only
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))
    except OSError:
        return False, f"Port {port} is occupied."
    finally:
        sock.close()
    return True, f"Port {port} is free."


def find_free_port(start: int, *, max_tries: int = 100) -> tuple[bool, int, str]:
    """Return ``(found, port, message)``, scanning up to ``max_tries`` ports
    from ``start``. Returns ``(False, 0, ...)`` on an invalid start or when no
    free port is found."""
    valid, _ = _validate_port(start)
    if not valid:
        return False, 0, f"Invalid start port: {start}."
    last = min(start + max_tries - 1, MAX_PORT)
    for candidate in range(start, last + 1):
        free, _ = check_port(candidate)
        if free:
            return True, candidate, f"Free port found: {candidate}."
    return False, 0, "No free port found."


# --- port persistence -----------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    """Load JSON config from ``path``; return ``{}`` when absent/unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(path: Path, config: dict[str, Any]) -> None:
    """Write ``config`` as pretty JSON to ``path`` (creating parent dirs)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def _env_path(config: LauncherConfig) -> Path:
    """Path of the ``.env`` file Docker Compose reads for this project.

    Compose loads ``.env`` from the project directory - the directory holding
    the compose file, which is ``install_dir`` when set and the current working
    directory otherwise (mirrors :attr:`LauncherConfig.compose_path`). Writing
    the port HERE, rather than only when ``install_dir`` is set, is what makes a
    port change actually reach Compose: otherwise :func:`set_port` would update
    only the launcher's own JSON and the running stack would keep publishing the
    old port (the launcher and Compose then disagree, and the app is unreachable
    on the launcher's port).
    """
    return config.compose_path.parent / ".env"


def _upsert_env_line(text: str, key: str, value: object) -> str:
    """Return ``text`` with ``key=value`` upserted (replacing one occurrence)."""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def _write_env(config: LauncherConfig, updates: dict[str, object]) -> None:
    """Upsert every ``key=value`` in ``updates`` into the Compose project's ``.env``.

    Best-effort: a write failure is logged and swallowed so it can never crash a
    port change.
    """
    if not updates:
        return
    env_file = _env_path(config)
    try:
        text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
        for key, value in updates.items():
            text = _upsert_env_line(text, key, value)
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write .env: %s", exc)


def _write_env_port(config: LauncherConfig, port: int) -> None:
    """Upsert only the public host port into ``.env`` (thin wrapper)."""
    _write_env(config, {config.env_port_key: port})


def _env_port_updates(config: LauncherConfig) -> dict[str, object]:
    """Every port var Compose needs: the public host port + each internal port."""
    updates: dict[str, object] = {config.env_port_key: resolve_port(config)}
    for name, key in config.env_internal_port_keys.items():
        updates[key] = resolve_internal_port(config, name)
    return updates


def _write_env_ports(config: LauncherConfig) -> None:
    """Write the public host port AND every configured internal port to ``.env``."""
    _write_env(config, _env_port_updates(config))


def resolve_port(config: LauncherConfig, cli_port: int | None = None) -> int:
    """Resolve the effective host port (first valid wins).

    Precedence: ``cli_port`` -> ``port`` in the launcher JSON config ->
    :attr:`LauncherConfig.default_port`.
    """
    if cli_port is not None and _validate_port(cli_port)[0]:
        return cli_port
    stored = load_config(config.launcher_config_file).get("port")
    if isinstance(stored, int) and _validate_port(stored)[0]:
        return stored
    return config.default_port


def set_port(config: LauncherConfig, port: int) -> tuple[bool, str]:
    """Validate and persist ``port`` into the launcher config (and ``.env``)."""
    valid, reason = _validate_port(port)
    if not valid:
        return False, reason
    data = load_config(config.launcher_config_file)
    data["port"] = port
    save_config(config.launcher_config_file, data)
    _write_env_ports(config)
    return True, _t(config, "port_set", port=port)


def resolve_internal_port(config: LauncherConfig, name: str) -> int:
    """Resolve an internal port: a stored override wins over the config default.

    Returns ``internal_ports[name]`` from the launcher config unless a valid
    override is stored under ``internal_ports`` in the launcher JSON. Returns
    ``0`` for an unknown name (no default to fall back to).
    """
    stored = load_config(config.launcher_config_file).get("internal_ports")
    if isinstance(stored, dict):
        value = stored.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and _validate_internal_port(value)[0]:
            return value
    return config.internal_ports.get(name, 0)


def set_internal_port(config: LauncherConfig, name: str, port: int) -> tuple[bool, str]:
    """Validate and persist an internal port (launcher JSON + ``.env``). No restart."""
    if name not in config.env_internal_port_keys:
        return False, _t(config, "internal_port_unknown", name=name)
    valid, reason = _validate_internal_port(port)
    if not valid:
        return False, reason
    data = load_config(config.launcher_config_file)
    stored = data.get("internal_ports")
    if not isinstance(stored, dict):
        stored = {}
    stored[name] = port
    data["internal_ports"] = stored
    save_config(config.launcher_config_file, data)
    _write_env_ports(config)
    return True, _t(config, "internal_port_set", name=name, port=port)


def change_internal_port(
    config: LauncherConfig,
    name: str,
    port: int,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
) -> tuple[bool, str]:
    """Change an internal container port - this REQUIRES an image rebuild.

    Unlike :func:`change_port` (the public host port, a seconds-fast no-rebuild
    recreate), an internal port is consumed when the image is built/started, so
    the chain rebuilds:

    1. validate the name + port and persist (launcher JSON + ``.env``);
    2. if the stack is running, STOP it, then ``up --build -d`` (minutes - the
       images are rebuilt with the new internal port);
    3. health-check on the public port.

    When the stack is not running this only persists (a later build picks it up).
    Returns ``(ok, message)``.
    """
    if name not in config.env_internal_port_keys:
        return False, _t(config, "internal_port_unknown", name=name)
    valid, reason = _validate_internal_port(port)
    if not valid:
        return False, reason
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")

    was_running = get_state(config) == "running"
    if was_running:
        stopped, stop_msg = stop(config)
        if not stopped:
            return False, stop_msg

    ok, msg = set_internal_port(config, name, port)
    if not ok:
        return False, msg
    if not was_running:
        return True, msg

    _notify(on_step, _t(config, "internal_port_rebuilding"))
    try:
        rc, tail = _stream_compose(
            config, "up", "--build", "-d", on_output=on_output, timeout=float(config.build_timeout)
        )
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "build_timeout")
    if rc != 0:
        return False, _t(config, "build_failed", detail=tail)
    if get_state(config) != "running":
        return False, _t(config, "start_no_container")

    _notify(on_step, _t(config, "checking_health"))
    healthy, detail = health_check(config)
    if not healthy:
        return False, _t(config, "not_reachable", detail=detail)
    _record_manifest(config, resolve_port(config), action="internal_port_change")
    return True, _t(config, "internal_port_changed", name=name, port=port)


def change_port(
    config: LauncherConfig,
    port: int,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
) -> tuple[bool, str]:
    """Change the host port and make a RUNNING stack actually serve on it.

    This is the missing half of :func:`set_port`: persisting the port is not
    enough, because a running container keeps its old published port until it is
    recreated. The chain:

    1. validate and persist the port (launcher JSON + ``.env``);
    2. if the stack is running, STOP it, then recreate with ``up -d`` - and
       deliberately NOT ``up --build -d``: only the published HOST port changed,
       the images are untouched, so the restart takes seconds rather than the
       minutes a rebuild would cost;
    3. health-check on the NEW port and report reachability.

    When the stack is not running this only persists the port (a later
    start/install picks it up). Returns ``(ok, message)``.
    """
    valid, reason = _validate_port(port)
    if not valid:
        return False, reason
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")

    was_running = get_state(config) == "running"
    if was_running:
        stopped, stop_msg = stop(config)
        if not stopped:
            return False, stop_msg

    ok, msg = set_port(config, port)
    if not ok:
        return False, msg
    if not was_running:
        return True, msg

    _notify(on_step, _t(config, "port_restarting"))
    try:
        rc, tail = _stream_compose(config, "up", "-d", on_output=on_output, timeout=float(config.start_timeout))
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "start_timeout")
    if rc != 0:
        return False, _t(config, "start_failed", detail=tail)
    if get_state(config) != "running":
        return False, _t(config, "start_no_container")

    _notify(on_step, _t(config, "checking_health"))
    healthy, detail = health_check(config, port)
    if not healthy:
        return False, _t(config, "not_reachable", detail=detail)
    _record_manifest(config, port, action="port_change")
    return True, _t(config, "port_changed", port=port)


# --- Lifecycle (install / start / stop / uninstall) -----------------------


def _compose_args(config: LauncherConfig, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        config.compose_project,
        "-f",
        str(config.compose_path),
        *args,
    ]


def _compose_cwd(config: LauncherConfig) -> Path | None:
    return Path(config.install_dir).expanduser() if config.install_dir else None


def _stream_compose(
    config: LauncherConfig, *args: str, on_output: OutputFn | None = None, timeout: float
) -> tuple[int, str]:
    return _stream_command(
        _compose_args(config, *args),
        on_output=on_output,
        timeout=timeout,
        cwd=_compose_cwd(config),
    )


def _call(config: LauncherConfig, hook: Callable[..., Any] | None) -> None:
    """Invoke an optional lifecycle callback; never let it break the action."""
    if hook is None:
        return
    try:
        hook(config)
    except Exception as exc:  # noqa: BLE001 - hooks must never break an action
        logger.warning("lifecycle callback failed: %s", exc)


def install(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
) -> tuple[bool, str]:
    """Build + start the stack, then VERIFY it is running and healthy.

    Guards (each returns ``(False, ...)``): invalid port, Docker down, missing
    compose file, occupied port. If the app is already running it returns
    ``(True, already_installed)``. Streams the build output through
    ``on_output``.
    """
    port = resolve_port(config)
    valid, reason = _validate_port(port)
    if not valid:
        return False, reason

    _call(config, config.on_before_install)
    _notify(on_step, _t(config, "checking_docker"))
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    if get_state(config) == "running":
        return True, _t(config, "already_installed")
    if not config.compose_path.is_file():
        return False, _t(config, "compose_not_found", path=config.compose_path)
    port_free, _ = check_port(port)
    if not port_free:
        return False, _t(config, "port_occupied", port=port)
    _write_env_ports(config)
    _notify(on_step, _t(config, "docker_ok"))

    _notify(on_step, _t(config, "building"))
    try:
        build_rc, build_tail = _stream_compose(
            config, "build", on_output=on_output, timeout=float(config.build_timeout)
        )
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "build_timeout")
    if build_rc != 0:
        return False, _t(config, "build_failed", detail=build_tail)
    _notify(on_step, _t(config, "image_built"))

    _notify(on_step, _t(config, "starting"))
    try:
        up_rc, up_tail = _stream_compose(config, "up", "-d", on_output=on_output, timeout=float(config.start_timeout))
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "start_timeout")
    if up_rc != 0:
        return False, _t(config, "start_failed", detail=up_tail)
    _notify(on_step, _t(config, "container_started"))

    _notify(on_step, _t(config, "checking_health"))
    if get_state(config) != "running":
        return False, _t(config, "container_not_running")
    healthy, health_msg = health_check(config)
    if not healthy:
        return False, _t(config, "not_reachable", detail=health_msg)
    _notify(on_step, _t(config, "health_ok"))
    _record_manifest(config, port, action="install")
    _call(config, config.on_after_install)
    return True, _t(config, "ready")


def ensure_installed(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
) -> tuple[bool, str]:
    """Single install entry point for the persistent window.

    For a generic app the compose file must already be present, so this is
    :func:`install`. It exists as a stable seam: an app that ships frozen
    binaries can wire a download step via ``config.on_before_install``.
    """
    return install(config, on_step=on_step, on_output=on_output)


def start(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
) -> tuple[bool, str]:
    """Start the stack via ``compose up --build -d``, then VERIFY it runs.

    Always passes ``--build`` so a code change is picked up on the next start;
    Docker's layer cache makes an unchanged rebuild near-instant. ``up --build
    -d`` also creates the containers if they do not exist yet, so it works from
    both 'stopped' and a removed state.
    """
    _call(config, config.on_before_start)
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    if get_state(config) == "running":
        return True, _t(config, "already_running")
    _notify(on_step, _t(config, "updating"))
    try:
        rc, tail = _stream_compose(
            config, "up", "--build", "-d", on_output=on_output, timeout=float(config.build_timeout)
        )
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "start_timeout")
    if rc != 0:
        return False, _t(config, "start_failed", detail=tail)
    if get_state(config) != "running":
        return False, _t(config, "start_no_container")
    existing = read_manifest(config) or {}
    _record_manifest(config, int(existing.get("port", resolve_port(config))), action="update")
    _call(config, config.on_after_start)
    return True, _t(config, "start_done")


def stop(config: LauncherConfig) -> tuple[bool, str]:
    """Stop the running containers, then VERIFY none are running.

    Uses ``docker stop`` by id so the containers REMAIN (state -> stopped),
    keeping data + images for a fast restart.
    """
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    state = get_state(config)
    if state == "not_installed":
        return False, _t(config, "not_installed")
    if state == "stopped":
        return True, _t(config, "already_stopped")
    running = _project_container_ids(config, running_only=True)
    try:
        _run(["docker", "stop", *running], timeout=float(config.stop_timeout) + 30.0)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, _t(config, "stop_failed", detail=str(exc))
    if _project_container_ids(config, running_only=True):
        return False, _t(config, "stop_not_verified")
    return True, _t(config, "stop_done")


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


def _project_containers(config: LauncherConfig, *, running_only: bool) -> list[tuple[str, str]]:
    """List this project's containers as ``(id, name)`` pairs."""
    cmd = ["docker", "ps"] if running_only else ["docker", "ps", "-a"]
    cmd += _name_filter_args(config)
    cmd += ["--format", "{{.ID}}\t{{.Names}}"]
    try:
        result = _run(cmd, timeout=15.0)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pairs: list[tuple[str, str]] = []
    for line in (result.stdout or "").strip().splitlines():
        cid, _, name = line.partition("\t")
        if cid:
            pairs.append((cid, name or cid))
    return pairs


def _project_images(config: LauncherConfig) -> list[tuple[str, str]]:
    """List this project's images as ``(id, reference)`` pairs, de-duped by id."""
    cmd = ["docker", "images"]
    for pat in config.image_patterns():
        cmd += ["--filter", f"reference=*{pat}*"]
    cmd += ["--format", "{{.ID}}\t{{.Repository}}"]
    try:
        result = _run(cmd, timeout=15.0)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in (result.stdout or "").strip().splitlines():
        cid, _, ref = line.partition("\t")
        if cid and cid not in seen:
            seen.add(cid)
            pairs.append((cid, ref or cid))
    return pairs


def _uninstall_images(config: LauncherConfig, on_step: ProgressFn | None = None) -> None:
    """Remove each of this project's images individually (verbose, best-effort)."""
    for cid, ref in _project_images(config):
        ok, detail = _docker_op(["docker", "image", "rm", "--force", cid], timeout=60.0)
        _notify(on_step, _step_label(config, _t(config, "step_remove_image", ref=ref), ok, detail))
        if not ok:
            logger.warning("image removal failed for %s: %s", ref, detail)


def uninstall(config: LauncherConfig, *, on_step: ProgressFn | None = None) -> tuple[bool, str]:
    """Force-remove containers (and images), then VERIFY they are gone.

    Verbose: every container stop/remove and every image removal is a separate
    step reported through ``on_step`` with a ``✓``/``✗`` result. Volumes are
    PRESERVED (data survives a reinstall).
    """
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    _notify(on_step, _t(config, "uninstalling"))
    containers = _project_containers(config, running_only=False)
    if not containers:
        _notify(on_step, _t(config, "no_containers"))
        _uninstall_images(config, on_step)
        mark_uninstalled(config, get_version(config))
        return True, _t(config, "nothing_to_uninstall")

    for cid, name in containers:
        ok, detail = _docker_op(["docker", "stop", cid], timeout=60.0)
        _notify(on_step, _step_label(config, _t(config, "step_stop_container", name=name), ok, detail))
    for cid, name in containers:
        ok, detail = _docker_op(["docker", "rm", "-f", cid], timeout=60.0)
        _notify(on_step, _step_label(config, _t(config, "step_remove_container", name=name), ok, detail))

    remaining = _project_container_ids(config, running_only=False)
    if remaining:
        _notify(on_step, _t(config, "verify_remain", count=len(remaining)))
        return False, _t(config, "uninstall_partial", count=len(remaining))
    _notify(on_step, _t(config, "verify_clean"))

    _uninstall_images(config, on_step)
    mark_uninstalled(config, get_version(config))
    return True, _t(config, "uninstall_done")


# --- Health + browser -----------------------------------------------------


def _health_probe(config: LauncherConfig, port: int) -> tuple[bool, str]:
    """One shot: ``(healthy, detail)``.

    Healthy == HTTP 200, and - when ``health_check_key`` is set - the JSON body
    has ``health_check_key == health_check_value``. An empty key means a 200 is
    enough.
    """
    url = f"http://localhost:{port}{config.health_check_path}"
    try:
        with urllib.request.urlopen(url, timeout=3.0) as resp:  # localhost only
            status = resp.status
            body = resp.read().decode("utf-8") if status == 200 else ""
    except Exception as exc:  # noqa: BLE001 - any failure means not-ready-yet
        return False, str(exc)
    if status != 200:
        if 500 <= status < 600:
            return False, f"server error (HTTP {status})"
        return False, f"HTTP {status}"
    if not config.health_check_key:
        return True, "reachable (HTTP 200)."
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, "invalid JSON response"
    if payload.get(config.health_check_key) == config.health_check_value:
        return True, f"reachable and healthy ({config.health_check_key}={config.health_check_value})."
    return False, f"response, but {config.health_check_key} != {config.health_check_value}"


def is_healthy(config: LauncherConfig, port: int | None = None) -> bool:
    """One-shot health check (no polling). True == healthy now."""
    return _health_probe(config, port if port is not None else resolve_port(config))[0]


def health_check(config: LauncherConfig, port: int | None = None) -> tuple[bool, str]:
    """Poll :func:`_health_probe` until healthy or the timeout elapses."""
    effective = port if port is not None else resolve_port(config)
    deadline = time.monotonic() + config.health_check_timeout
    last = "no response"
    while time.monotonic() < deadline:
        ok, detail = _health_probe(config, effective)
        if ok:
            return True, detail
        last = detail
        time.sleep(1.0)
    return False, _t(config, "not_reachable_after", timeout=config.health_check_timeout, detail=last)


def open_browser(config: LauncherConfig, port: int | None = None) -> None:
    """Open the app in the default browser. Never raises."""
    effective = port if port is not None else resolve_port(config)
    url = f"http://localhost:{effective}{config.browser_path}"
    logger.debug("open browser: %s", url)
    try:
        webbrowser.open(url)
    except OSError as exc:
        logger.warning("could not open browser: %s", exc)


# --- Version --------------------------------------------------------------


def get_version(config: LauncherConfig) -> str:
    """Return the recorded app version (manifest), else the launcher version."""
    data = read_manifest(config)
    if data and data.get("app_version"):
        return str(data["app_version"])
    return __version__


# --- Install manifest -----------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_manifest(config: LauncherConfig) -> dict[str, Any] | None:
    """Read the install manifest, or ``None`` if absent/malformed (fail-open)."""
    try:
        data = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_manifest(config: LauncherConfig, data: dict[str, Any]) -> None:
    path = config.manifest_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def collect_installed_artifacts(config: LauncherConfig) -> dict[str, list[Any]]:
    """Snapshot the docker artifacts belonging to this project."""
    containers: list[dict[str, str]] = []
    try:
        result = _run(
            ["docker", "ps", "-a", "--filter", f"name={config.container_name}", "--format", "{{.Names}}\t{{.Image}}"],
            timeout=15.0,
        )
        for line in (result.stdout or "").strip().splitlines():
            name, _, image = line.partition("\t")
            if name:
                containers.append({"name": name, "image": image})
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {
        "containers": containers,
        "images": _image_refs(config, (config.image_name,)),
        "volumes": _docker_names(config, "volume", (config.container_name,)),
    }


def write_manifest(config: LauncherConfig, version: str) -> None:
    """Write/refresh the install manifest after a successful install/rebuild.

    Preserves ``installed_at`` and the append-only ``install_history``;
    refreshes ``updated_at`` and the artifact lists. Never raises.
    """
    try:
        existing = read_manifest(config) or {}
        arts = collect_installed_artifacts(config)
        data: dict[str, Any] = {
            "schema": 1,
            "app_name": config.app_name,
            "app_version": version,
            "version": version,  # legacy alias
            "launcher_version": __version__,
            "install_dir": config.install_dir,
            "installed_at": existing.get("installed_at") or _now(),
            "updated_at": _now(),
            "status": "installed",
            "port": resolve_port(config),
            "compose_project": config.compose_project,
            "compose_file": str(config.compose_path),
            "containers": arts["containers"],
            "images": arts["images"],
            "volumes": arts["volumes"],
            "config_files": [str(config.launcher_config_file)],
            "install_history": list(existing.get("install_history", [])),
        }
        _write_manifest(config, data)
    except OSError as exc:
        logger.warning("install-manifest write failed: %s", exc)


def append_history(config: LauncherConfig, action: str, version: str) -> None:
    """Append one entry to the manifest's ``install_history`` audit trail."""
    data = read_manifest(config) or {}
    history = list(data.get("install_history", []))
    history.append({"action": action, "version": version, "at": _now()})
    data["install_history"] = history
    with contextlib.suppress(OSError):
        _write_manifest(config, data)


def mark_uninstalled(config: LauncherConfig, version: str) -> None:
    """Mark the install as uninstalled and clear the artifact lists.

    Keeps the audit trail so a later cleanup scan finds nothing for this
    install. No-op when no manifest exists.
    """
    data = read_manifest(config)
    if data is None:
        return
    history = list(data.get("install_history", []))
    history.append({"action": "uninstall", "version": version, "at": _now()})
    data.update(
        {
            "install_history": history,
            "status": "uninstalled",
            "uninstalled_at": _now(),
            "containers": [],
            "images": [],
            "volumes": [],
        }
    )
    with contextlib.suppress(OSError):
        _write_manifest(config, data)


def _record_manifest(config: LauncherConfig, port: int, *, action: str) -> None:
    """Best-effort: (re)write the manifest + append a history entry. Never raises."""
    try:
        version = get_version(config)
        write_manifest(config, version)
        # Pin the exact port this lifecycle action used (write_manifest records
        # the resolved port; they usually match, but keep them consistent).
        latest = read_manifest(config)
        if latest is not None and latest.get("port") != port:
            latest["port"] = port
            _write_manifest(config, latest)
        append_history(config, action, version)
    except OSError as exc:
        logger.warning("manifest record failed: %s", exc)


def manifest_artifacts(config: LauncherConfig) -> dict[str, list[Any]]:
    """Return the artifacts the manifest currently records (active install)."""
    data = read_manifest(config)
    if data is None or data.get("status") == "uninstalled":
        return {"containers": [], "images": [], "volumes": [], "configs": []}
    containers = [c.get("name", "") if isinstance(c, dict) else str(c) for c in data.get("containers", [])]
    return {
        "containers": [c for c in containers if c],
        "images": list(data.get("images", [])),
        "volumes": list(data.get("volumes", [])),
        "configs": list(data.get("config_files", [])),
    }


# --- Cleanup --------------------------------------------------------------


def _running_container_names(config: LauncherConfig) -> list[str]:
    try:
        result = _run(["docker", "ps", "--format", "{{.Names}}", *_name_filter_args(config)])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [n for n in (result.stdout or "").strip().splitlines() if n]


def _docker_names(config: LauncherConfig, kind: str, patterns: tuple[str, ...]) -> list[str]:
    """List docker object names matching any of ``patterns`` (de-duped)."""
    if kind == "container":
        base = ["docker", "ps", "-a", "--format", "{{.Names}}"]
    else:  # volume
        base = ["docker", "volume", "ls", "--format", "{{.Name}}"]
    found: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        if not pat:
            continue
        try:
            result = _run([*base, "--filter", f"name={pat}"], timeout=15.0)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        for name in (result.stdout or "").strip().splitlines():
            if name and name not in seen:
                seen.add(name)
                found.append(name)
    return found


def _image_refs(config: LauncherConfig, patterns: tuple[str, ...]) -> list[str]:
    """List image references (``repo:tag``) matching any of ``patterns``."""
    found: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        if not pat:
            continue
        try:
            result = _run(
                ["docker", "images", "--filter", f"reference=*{pat}*", "--format", "{{.Repository}}:{{.Tag}}"],
                timeout=15.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        for ref in (result.stdout or "").strip().splitlines():
            if ref and ref not in seen:
                seen.add(ref)
                found.append(ref)
    return found


def _stale_config_dirs(config: LauncherConfig, active_configs: list[str]) -> list[str]:
    """Existing ``cleanup_configs`` dirs not referenced by the active manifest."""
    active = {str(Path(c).expanduser()) for c in active_configs}
    active.add(str(config.config_path))  # never target the live config dir
    out: list[str] = []
    for candidate in config.cleanup_configs:
        resolved = Path(candidate).expanduser()
        if resolved.exists() and str(resolved) not in active:
            out.append(str(resolved))
    return out


def find_stale_artifacts(config: LauncherConfig) -> dict[str, list[Any]]:
    """Find STALE (leftover) artifacts to offer for cleanup at startup.

    Manifest-first: the current install's recorded artifacts are EXCLUDED -
    only artifacts beyond it (old versions, legacy names, orphans) are
    returned. Without a manifest, currently-RUNNING containers are protected.
    """
    active = manifest_artifacts(config)
    active_containers = set(active["containers"])
    active_images = set(active["images"])
    active_volumes = set(active["volumes"])
    if not (active_containers or active_images or active_volumes):
        active_containers |= set(_running_container_names(config))

    patterns = tuple(config.cleanup_patterns())
    return {
        "containers": [n for n in _docker_names(config, "container", patterns) if n not in active_containers],
        "images": [r for r in _image_refs(config, patterns) if r not in active_images],
        "volumes": [v for v in _docker_names(config, "volume", patterns) if v not in active_volumes],
        "configs": _stale_config_dirs(config, active.get("configs", [])),
    }


def has_stale_artifacts(stale: dict[str, list[Any]]) -> bool:
    """True when any stale category is non-empty."""
    return any(stale.get(k) for k in ("containers", "images", "volumes", "configs"))


def cleanup_offer_lines(config: LauncherConfig, stale: dict[str, list[Any]]) -> list[str]:
    """Human-readable summary lines for the in-window cleanup offer."""
    labels = (
        ("containers", "Container"),
        ("images", "Image(s)"),
        ("volumes", "Volume(s)"),
        ("configs", "Config dir(s)"),
    )
    lines: list[str] = []
    for key, label in labels:
        items = stale.get(key, [])
        if items:
            lines.append(f"{len(items)} {label}: " + ", ".join(str(i) for i in items))
    return lines


def _human_size(num_bytes: int) -> str:
    """Format a byte count the way Docker does (decimal, e.g. ``245 MB``)."""
    if num_bytes <= 0:
        return "0 B"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}"
        size /= 1000
    return f"{size:.0f} TB"


def _image_size_bytes(ref: str) -> int:
    """Disk size of a docker image in bytes, or ``0`` when undeterminable."""
    try:
        result = _run(["docker", "image", "inspect", ref, "--format", "{{.Size}}"], timeout=15.0)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    try:
        return int((result.stdout or "").strip())
    except ValueError:
        return 0


def _remove_config_path(path: str) -> tuple[bool, str]:
    """Delete a stale config file or directory. Never raises."""
    target = Path(path).expanduser()
    try:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        return True, ""
    except OSError as exc:
        return False, str(exc)


def cleanup_stale(
    config: LauncherConfig,
    selected: dict[str, list[Any]],
    *,
    on_step: ProgressFn | None = None,
    remove_volumes_too: bool = False,
) -> tuple[bool, str]:
    """Remove the STALE artifacts in ``selected`` (from :func:`find_stale_artifacts`).

    Verbose: a discovery line per category, then a SEPARATE ``on_step`` line per
    container / image / config dir (and, when ``remove_volumes_too``, per
    volume) carrying a ``✓``/``✗`` result, then a closing summary. Volumes are
    DATA - skipped unless the caller opts in. Best-effort.
    """
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")

    containers = selected.get("containers", [])
    images = selected.get("images", [])
    volumes = selected.get("volumes", [])
    configs = selected.get("configs", [])

    _notify(on_step, _t(config, "cleanup_running"))
    _notify(on_step, _t(config, "scan_containers", count=len(containers)))
    _notify(on_step, _t(config, "scan_images", count=len(images)))
    _notify(on_step, _t(config, "scan_volumes", count=len(volumes)))
    _notify(on_step, _t(config, "scan_configs", count=len(configs)))

    removed = 0
    failures = 0
    freed_bytes = 0

    for name in containers:
        ok, detail = _docker_op(["docker", "rm", "-f", name], timeout=60.0)
        _notify(on_step, _step_label(config, _t(config, "step_remove_container", name=name), ok, detail))
        removed += 1 if ok else 0
        failures += 0 if ok else 1
    for ref in images:
        size = _image_size_bytes(ref)
        ok, detail = _docker_op(["docker", "image", "rm", "--force", ref], timeout=60.0)
        size_note = f" ({_human_size(size)})" if ok and size > 0 else ""
        _notify(on_step, _step_label(config, _t(config, "step_remove_image", ref=ref), ok, detail) + size_note)
        if ok:
            removed += 1
            freed_bytes += size
        else:
            failures += 1
    if remove_volumes_too:
        for vol in volumes:
            ok, detail = _docker_op(["docker", "volume", "rm", vol], timeout=30.0)
            _notify(on_step, _step_label(config, _t(config, "step_remove_volume", name=vol), ok, detail))
            removed += 1 if ok else 0
            failures += 0 if ok else 1
    for path in configs:
        ok, detail = _remove_config_path(path)
        _notify(on_step, _step_label(config, _t(config, "step_remove_config", path=path), ok, detail))
        removed += 1 if ok else 0
        failures += 0 if ok else 1

    freed = _human_size(freed_bytes)
    _notify(on_step, _t(config, "data_preserved"))
    if failures:
        return False, _t(config, "cleanup_partial", count=failures)
    return True, _t(config, "cleanup_done", count=removed, freed=freed)
