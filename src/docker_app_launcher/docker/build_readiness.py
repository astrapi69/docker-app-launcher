"""Capability gates for the build paths: can this toolchain actually build
and start THIS project - not merely does each tool exist (#54).

Error class, present is not functional: a check that only proves an artifact
exists (binary there, file there, plugin there) proves nothing about the
capability actually needed. The QA device had the compose plugin present,
the old ladder went green, and the build was still impossible because buildx
was 0.8.2. Every gate here checks the CAPABILITY and COLLECTS every
missing/too-old link into one message, so a single run shows all gaps
instead of revealing them one failed build at a time.

Two requirement sources, kept separate and attributed in the message:

* Intrinsic (launcher): what the launcher needs for the mode - compose mode
  needs buildx >= 0.17 once compose is new enough to gate it. Non-negotiable.
* App-declared (config): what the app's Dockerfile / compose file demands
  (:attr:`LauncherConfig.min_engine_version` and friends). The effective
  requirement is the MAXIMUM of the two: config can only raise the bar,
  never lower it.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import py_client
from docker_app_launcher.docker.command_runner import _t
from docker_app_launcher.docker.compose_runtime import compose_available
from docker_app_launcher.docker.tool_versions import (
    ToolVersions,
    detect_tool_versions,
    intrinsic_buildx_requirement,
    parse_version,
)

logger = logging.getLogger("docker_app_launcher.docker.build_readiness")

# The distribution-independent buildx install location. Package sources have
# proven unreliable, so the readiness message names the portable path.
BUILDX_PLUGIN_PATH = "~/.docker/cli-plugins/docker-buildx"


@dataclass(frozen=True)
class _Requirement:
    component: str  # "engine" | "api" | "compose" | "buildx"
    required: Version
    found: Version | None
    source: str  # "launcher" | "app"

    @property
    def unmet(self) -> bool:
        return self.found is None or self.found < self.required


def _effective(intrinsic: Version | None, declared: Version | None, component: str) -> tuple[Version | None, str]:
    """Merge the launcher's intrinsic floor with the app's declared one.

    Effective = max(intrinsic, declared); the source attribution follows
    whichever won. A declared value BELOW the intrinsic floor cannot lower
    the gate: it is warned about and the intrinsic value applies.
    """
    if intrinsic is not None and declared is not None:
        if declared < intrinsic:
            logger.warning(
                "config min_%s_version=%s is below the launcher's intrinsic floor %s; "
                "the intrinsic value applies (config can only raise the bar)",
                component,
                declared,
                intrinsic,
            )
            return intrinsic, "launcher"
        return (declared, "app") if declared > intrinsic else (intrinsic, "launcher")
    if intrinsic is not None:
        return intrinsic, "launcher"
    if declared is not None:
        return declared, "app"
    return None, ""


def _version_blocker(config: LauncherConfig, req: _Requirement) -> str:
    """One localized, source-attributed blocker line for an unmet requirement."""
    required = str(req.required)
    if req.component == "buildx":
        # buildx exists only because the compose build path needs it, so the
        # message names the compose mode and the portable install path -
        # whether the floor is the launcher's 0.17 or an app-raised value.
        if req.found is None:
            return _t(config, "buildx_missing", required=required, path=BUILDX_PLUGIN_PATH)
        return _t(config, "buildx_too_old", required=required, found=str(req.found), path=BUILDX_PLUGIN_PATH)
    # App-declared engine / api / compose floor: attribute it to the app so
    # the user can tell an app demand from a launcher-intrinsic one.
    if req.found is None:
        return _t(config, "app_requirement_missing", component=req.component, required=required)
    return _t(config, "app_requirement_too_old", component=req.component, required=required, found=str(req.found))


def _compose_requirements(config: LauncherConfig, tv: ToolVersions, *, plugin_path: bool) -> list[_Requirement]:
    """Every version requirement the compose build path must satisfy."""
    reqs: list[_Requirement] = []
    # buildx: intrinsic only on the plugin path (legacy v1 does not use bake).
    intrinsic_buildx = intrinsic_buildx_requirement(tv.compose) if plugin_path else None
    declared_buildx = parse_version(config.min_buildx_version) if config.min_buildx_version else None
    eff_buildx, source = _effective(intrinsic_buildx, declared_buildx, "buildx")
    if eff_buildx is not None:
        reqs.append(_Requirement("buildx", eff_buildx, tv.buildx, source))
    # engine / api / compose: app-declared only (no intrinsic floor here).
    for component, declared_raw, found in (
        ("engine", config.min_engine_version, tv.engine),
        ("api", config.min_api_version, tv.api),
        ("compose", config.min_compose_version, tv.compose),
    ):
        declared = parse_version(declared_raw) if declared_raw else None
        if declared is not None:
            reqs.append(_Requirement(component, declared, found, "app"))
    return reqs


def compose_blockers(config: LauncherConfig) -> list[str]:
    """All reasons the compose build path cannot build this project, collected.

    Empty list == ready. Order: the compose file, then the frontend, then the
    tool versions - but nothing short-circuits, so a run with a missing file
    AND an old buildx reports both.
    """
    blockers: list[str] = []
    # 1. The project itself: a compose file present and readable.
    if not config.compose_path.is_file():
        blockers.append(_t(config, "compose_not_found", path=config.compose_path))
    else:
        try:
            config.compose_path.read_text(encoding="utf-8")
        except OSError as exc:
            blockers.append(_t(config, "compose_unreadable", path=config.compose_path, error=exc))
    # 2. A usable compose frontend.
    usable, verdict = compose_available(config)
    plugin_path = usable and verdict == "plugin"
    if not usable:
        if verdict == "legacy_incompatible":
            blockers.append(_t(config, "compose_v1_incompatible", path=config.compose_path))
        else:
            blockers.append(_t(config, "compose_missing"))
    # 3. Tool versions: the intrinsic buildx floor + any app-declared floors.
    tv = detect_tool_versions(config)
    blockers.extend(
        _version_blocker(config, req) for req in _compose_requirements(config, tv, plugin_path=plugin_path) if req.unmet
    )
    # 4. Enough disk for the build (advisory, G4).
    disk = _disk_blocker(config, config.compose_path.parent)
    if disk is not None:
        blockers.append(disk)
    return blockers


def dockerfile_blockers(config: LauncherConfig) -> list[str]:
    """All reasons the dockerfile build path cannot build this project (#51/#54).

    Capability, not existence, here too: docker-py importable, Dockerfile
    present and readable, build context resolvable, plus any app-declared
    engine/API floor. The classic builder used by docker-py needs no buildx,
    so there is no buildx gate in this mode.
    """
    blockers: list[str] = []
    if not py_client.available():
        blockers.append(_t(config, "dockerfile_mode_needs_dockerpy"))
    if not config.dockerfile_path.is_file():
        blockers.append(_t(config, "dockerfile_not_found", path=config.dockerfile_path))
    else:
        try:
            config.dockerfile_path.read_text(encoding="utf-8")
        except OSError as exc:
            blockers.append(_t(config, "dockerfile_unreadable", path=config.dockerfile_path, error=exc))
    if not config.build_context_path.is_dir():
        blockers.append(_t(config, "build_context_not_found", path=config.build_context_path))
    tv = detect_tool_versions(config)
    for component, declared_raw, found in (
        ("engine", config.min_engine_version, tv.engine),
        ("api", config.min_api_version, tv.api),
    ):
        declared = parse_version(declared_raw) if declared_raw else None
        if declared is not None:
            req = _Requirement(component, declared, found, "app")
            if req.unmet:
                blockers.append(_version_blocker(config, req))
    disk = _disk_blocker(config, config.build_context_path)  # advisory (G4)
    if disk is not None:
        blockers.append(disk)
    return blockers


def _human_bytes(n: int) -> str:
    """A compact human size (``1.8 GB``) for the disk message."""
    step = 1000.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= step
    return f"{value:.1f} PB"


def _disk_blocker(config: LauncherConfig, base: Path) -> str | None:
    """An advisory blocker when free space on ``base`` is clearly insufficient
    for a build (G4, #61). ``min_build_disk_bytes <= 0`` disables the check;
    a base whose free space cannot be read is skipped (never a false block)."""
    floor = config.min_build_disk_bytes
    if floor <= 0:
        return None
    probe = base
    while not probe.exists() and probe != probe.parent:  # base may not exist yet
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return None
    if free < floor:
        return _t(config, "disk_low", free=_human_bytes(free), needed=_human_bytes(floor), path=base)
    return None


def join_blockers(blockers: list[str]) -> str:
    """Combine collected blockers into one message body."""
    return "\n".join(blockers)
