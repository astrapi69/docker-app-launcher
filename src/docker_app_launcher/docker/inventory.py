"""Which docker objects belong to THIS app - containers, images, volumes.

Pure enumeration by the config-derived name/reference filters, shared by
lifecycle (uninstall), cleanup (stale scan) and the install manifest.
Nothing here modifies anything.
"""

from __future__ import annotations

import logging
import subprocess

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker.command_runner import _run

logger = logging.getLogger("docker_app_launcher.docker.inventory")


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


def _project_volumes(config: LauncherConfig) -> list[str]:
    """Volumes belonging to the active Compose project (``<compose_project>_*``).

    These are NEVER offered or removed by cleanup; the launcher reports them as
    protected so the user always sees why they were left alone.
    """
    prefix = f"{config.compose_project}_" if config.compose_project else ""
    if not prefix:
        return []
    return [v for v in _docker_names(config, "volume", tuple(config.cleanup_patterns())) if v.startswith(prefix)]


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
