"""Find and remove leftovers of PREVIOUS installations - never live data.

Enumerates stale containers/images/volumes/config dirs (the active
project's data volumes are excluded unconditionally, #11) and removes
exactly what the user selected, step by step, with sizes.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker.command_runner import (
    ProgressFn,
    ProgressPctFn,
    _docker_op,
    _notify,
    _progress,
    _step_label,
    _t,
)
from docker_app_launcher.docker.detection import check_docker
from docker_app_launcher.docker.inventory import (
    _docker_names,
    _image_refs,
    _image_size_bytes,
    _project_volumes,
    _running_container_names,
)
from docker_app_launcher.install_manifest import manifest_artifacts

logger = logging.getLogger("docker_app_launcher.docker.cleanup")


def _searched_config_dirs(config: LauncherConfig, seen: set[str]) -> list[str]:
    """Scan ``cleanup_search_paths`` for ``legacy_names`` subdirectories.

    For each base directory and legacy name, both ``<base>/<name>`` and the
    dotted ``<base>/.<name>`` are checked, so a base of ``~/.config`` finds
    ``~/.config/<name>`` and a base of ``~`` finds ``~/.<name>``. Already-seen
    paths (explicit ``cleanup_configs`` and the live config dir) are skipped.
    """
    out: list[str] = []
    for base in config.cleanup_search_paths:
        base_dir = Path(base).expanduser()
        for name in config.legacy_names:
            for candidate in (base_dir / name, base_dir / f".{name}"):
                resolved = str(candidate)
                if candidate.exists() and resolved not in seen:
                    seen.add(resolved)
                    out.append(resolved)
    return out


def _stale_config_dirs(config: LauncherConfig, active_configs: list[str]) -> list[str]:
    """Stale config dirs: explicit ``cleanup_configs`` plus ``cleanup_search_paths`` hits.

    Excludes anything the active manifest references and the live config dir.
    """
    seen = {str(Path(c).expanduser()) for c in active_configs}
    seen.add(str(config.config_path))  # never target the live config dir
    out: list[str] = []
    for candidate in config.cleanup_configs:
        resolved = str(Path(candidate).expanduser())
        if Path(resolved).exists() and resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    out.extend(_searched_config_dirs(config, seen))
    return out


def find_stale_artifacts(config: LauncherConfig) -> dict[str, list[Any]]:
    """Find STALE (leftover) artifacts to offer for cleanup at startup.

    Manifest-first: the current install's recorded artifacts are EXCLUDED -
    only artifacts beyond it (old versions, legacy names, orphans) are
    returned. Without a manifest, currently-RUNNING containers are protected.

    The active install's own Compose volumes (named ``<compose_project>_*``) are
    ALWAYS excluded, unconditionally and independent of the manifest or whether
    containers currently exist - they hold live user data and must never be
    offered for deletion (deleting one while its container runs also blocks
    ``docker volume rm`` indefinitely). Legacy volumes (a different prefix) are
    still offered.
    """
    active = manifest_artifacts(config)
    active_containers = set(active["containers"])
    active_images = set(active["images"])
    active_volumes = set(active["volumes"])
    if not (active_containers or active_images or active_volumes):
        active_containers |= set(_running_container_names(config))

    patterns = tuple(config.cleanup_patterns())
    project_prefix = f"{config.compose_project}_" if config.compose_project else ""
    volumes: list[str] = []
    for vol in _docker_names(config, "volume", patterns):
        if vol in active_volumes:
            continue
        if project_prefix and vol.startswith(project_prefix):
            logger.debug("cleanup: protecting active-project volume %s (prefix %r)", vol, project_prefix)
            continue
        volumes.append(vol)
    return {
        "containers": [n for n in _docker_names(config, "container", patterns) if n not in active_containers],
        "images": [r for r in _image_refs(config, patterns) if r not in active_images],
        "volumes": volumes,
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
    on_progress: ProgressPctFn | None = None,
    remove_volumes_too: bool = False,
) -> tuple[bool, str]:
    """Remove the STALE artifacts in ``selected`` (from :func:`find_stale_artifacts`).

    Verbose: a discovery line per category, then a SEPARATE ``on_step`` line per
    container / image / config dir (and, when ``remove_volumes_too``, per
    volume) carrying a ``✓``/``✗`` result, then a closing summary. Volumes are
    DATA - skipped unless the caller opts in. ``on_progress`` gets a determinate
    bar over the removable artifacts. Best-effort.
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
    total_steps = len(containers) + len(images) + len(configs) + (len(volumes) if remove_volumes_too else 0)
    done = 0

    def _bump(label: str) -> None:
        nonlocal done
        done += 1
        if total_steps > 0:
            _progress(on_progress, min(done * 100 // total_steps, 100), label)

    _progress(on_progress, 0, _t(config, "cleanup_running"))
    for name in containers:
        ok, detail = _docker_op(["docker", "rm", "-f", name], timeout=60.0)
        _notify(on_step, _step_label(config, _t(config, "step_remove_container", name=name), ok, detail))
        removed += 1 if ok else 0
        failures += 0 if ok else 1
        _bump(_t(config, "step_remove_container", name=name))
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
        _bump(_t(config, "step_remove_image", ref=ref))
    # Volumes are DATA. The active project's own volumes are NEVER touched
    # (removing one while its container runs blocks ``docker volume rm``); every
    # volume gets an explicit line so the run never looks stalled.
    project_volumes = _project_volumes(config)
    project_set = set(project_volumes)
    if remove_volumes_too:
        for vol in volumes:
            if vol in project_set:
                continue  # active-project volume; reported below, never removed
            ok, detail = _docker_op(["docker", "volume", "rm", vol], timeout=30.0)
            _notify(on_step, _step_label(config, _t(config, "step_remove_volume", name=vol), ok, detail))
            removed += 1 if ok else 0
            failures += 0 if ok else 1
            _bump(_t(config, "step_remove_volume", name=vol))
    else:
        for vol in volumes:
            if vol not in project_set:
                _notify(on_step, _t(config, "step_skip_volume", name=vol))
    for vol in project_volumes:
        _notify(on_step, _t(config, "step_skip_volume_active", name=vol))
    for path in configs:
        ok, detail = _remove_config_path(path)
        _notify(on_step, _step_label(config, _t(config, "step_remove_config", path=path), ok, detail))
        removed += 1 if ok else 0
        failures += 0 if ok else 1
        _bump(_t(config, "step_remove_config", path=path))

    freed = _human_size(freed_bytes)
    _notify(on_step, _t(config, "data_preserved"))
    _progress(on_progress, 100, _t(config, "data_preserved"))
    if failures:
        return False, _t(config, "cleanup_partial", count=failures)
    return True, _t(config, "cleanup_done", count=removed, freed=freed)
