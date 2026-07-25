"""The install manifest: what THIS launcher installed, for precise cleanup.

Records containers/images/volumes/config dirs at install time so cleanup
never guesses, plus the install history and the uninstall marker.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

from docker_app_launcher import __version__
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker.command_runner import _run
from docker_app_launcher.docker.inventory import _docker_names, _image_refs
from docker_app_launcher.launcher_settings import resolve_port

logger = logging.getLogger("docker_app_launcher.install_manifest")


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
        # Lazy import: lifecycle writes the manifest (lifecycle -> manifest is
        # the module dependency direction); this one version lookup must not
        # invert it into an import cycle.
        from docker_app_launcher.docker.lifecycle import get_version

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
