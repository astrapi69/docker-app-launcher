"""launcher.json persistence and validation: ports, locale, window geometry.

Everything the launcher remembers between runs, plus the ``.env`` writing
that keeps the launcher and Docker Compose agreeing on ports (#3).
"""

from __future__ import annotations

import json
import logging
import re
import socket
from pathlib import Path
from typing import Any

from docker_app_launcher.config import SUPPORTED_LOCALES, LauncherConfig, detect_system_locale
from docker_app_launcher.docker.command_runner import _t

logger = logging.getLogger("docker_app_launcher.launcher_settings")


MIN_PORT = 1024

MAX_PORT = 65535

# Internal (container) ports are not published on the host, so they are not
# bound by the 1024 floor a host-published port needs (e.g. nginx :80).
MIN_INTERNAL_PORT = 1


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


def resolve_locale(config: LauncherConfig) -> str:
    """Resolve the effective UI locale (the picker's persisted choice wins).

    Precedence: ``locale`` in the launcher JSON (the user's last choice) ->
    :attr:`LauncherConfig.locale`. ``"auto"`` (stored or default) resolves to the
    system locale; an unsupported value falls back to English.
    """
    stored = load_config(config.launcher_config_file).get("locale")
    candidate = stored if isinstance(stored, str) and stored else config.locale
    if candidate == "auto":
        candidate = detect_system_locale()
    return candidate if candidate in SUPPORTED_LOCALES else "en"


def set_locale(config: LauncherConfig, locale: str) -> str:
    """Persist the chosen UI ``locale`` into the launcher JSON; return it."""
    data = load_config(config.launcher_config_file)
    data["locale"] = locale
    save_config(config.launcher_config_file, data)
    return locale


_GEOMETRY_RE = re.compile(r"^\d+x\d+[+-]-?\d+[+-]-?\d+$")


def set_window_geometry(config: LauncherConfig, geometry: str) -> None:
    """Persist the window geometry (``WxH+X+Y``) so the next start reopens
    the window where the user left it. Invalid strings are ignored."""
    if not _GEOMETRY_RE.match(geometry or ""):
        return
    data = load_config(config.launcher_config_file)
    data["window_geometry"] = geometry
    save_config(config.launcher_config_file, data)


def resolve_window_geometry(config: LauncherConfig) -> str:
    """The stored window geometry from the launcher JSON, or ``""``."""
    value = load_config(config.launcher_config_file).get("window_geometry", "")
    return value if isinstance(value, str) and _GEOMETRY_RE.match(value) else ""


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


def _compose_cwd(config: LauncherConfig) -> Path | None:
    return Path(config.install_dir).expanduser() if config.install_dir else None
