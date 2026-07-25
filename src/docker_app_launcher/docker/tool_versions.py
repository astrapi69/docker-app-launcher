"""The Docker toolchain versions - engine, CLI, compose, buildx - and the
intrinsic buildx floor the compose build path needs (#54).

The compose path has an unwritten version chain: engine -> CLI -> compose
plugin -> buildx. Each link ages independently on a user's machine, and a
present plugin proves nothing about the capability to build. Modern
``docker compose build`` delegates to ``buildx bake`` and, from Compose
**v2.40.2** (PR docker/compose#13295), HARD-refuses when buildx < 0.17.0
with ``compose build requires buildx 0.17 or later``
(``pkg/compose/build_bake.go`` -> ``getBuildxPlugin``; the constant
``buildxMinVersion = "0.17.0"`` lives in ``pkg/compose/api_versions.go``).

This module reads every link's version once (cached per process, logged as
one line so future bug reports show the whole chain), parses the dirty
real-world strings with a real version library (``packaging`` - never a
string compare, never a self-built parser), and answers the launcher's
intrinsic question: does the DETECTED compose plugin actually require a
newer buildx than the one installed?

Attribution split (the readiness layer builds messages on top):

* Intrinsic (launcher): compose mode needs buildx >= 0.17 when compose is
  new enough to gate it. Not negotiable, not configurable down.
* App-declared: what the app's own Dockerfile / compose file demands - lives
  in :class:`LauncherConfig`, handled by :mod:`build_readiness`.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from docker_app_launcher.config import LauncherConfig, normalize_version_core
from docker_app_launcher.docker.command_runner import _run

logger = logging.getLogger("docker_app_launcher.docker.tool_versions")

# docker/compose api_versions.go: buildxMinVersion.
BUILDX_MIN_VERSION = Version("0.17.0")
# The first Compose that HARD-gates buildx >= 0.17 (v2.40.2, PR #13295).
# Below this, ``docker compose build`` does not emit the version error, so
# blocking there would be a false positive - we mirror upstream's own line.
COMPOSE_BUILDX_GATE = Version("2.40.2")


def parse_version(raw: str) -> Version | None:
    """A comparable :class:`~packaging.version.Version`, or ``None``.

    Normalizes the dirty string to its dotted-numeric core first
    (``v0.8.2-docker`` -> ``0.8.2``, ``20.10.21+dfsg1`` -> ``20.10.21``),
    then lets ``packaging`` do the real parse and comparison. A string with
    no version in it (``"latest"``) yields ``None``.
    """
    core = normalize_version_core(raw or "")
    if core is None:
        return None
    try:
        return Version(core)
    except InvalidVersion:  # pragma: no cover - the regex already constrains the core
        return None


@dataclass(frozen=True)
class ToolVersions:
    """Every link's raw version string plus its parsed form (``None`` when the
    tool is absent or its version could not be read)."""

    engine_raw: str = ""
    engine: Version | None = None
    cli_raw: str = ""
    cli: Version | None = None
    api_raw: str = ""
    api: Version | None = None
    compose_raw: str = ""
    compose: Version | None = None
    buildx_raw: str = ""
    buildx: Version | None = None

    def log_line(self) -> str:
        """The one-line chain summary for the log (device forensics, #54).

        Shows each link's parsed version (clean and comparable); the raw
        buildx line - module path plus git sha - would only add noise here
        and is still in the DEBUG command log.
        """

        def show(parsed: Version | None, raw: str) -> str:
            if parsed is not None:
                return str(parsed)
            return raw or "-"

        return (
            f"engine={show(self.engine, self.engine_raw)} api={show(self.api, self.api_raw)} "
            f"cli={show(self.cli, self.cli_raw)} compose={show(self.compose, self.compose_raw)} "
            f"buildx={show(self.buildx, self.buildx_raw)}"
        )


_VERSIONS: ToolVersions | None = None


def reset_versions_cache() -> None:
    """Forget the probed toolchain (tests / re-checks)."""
    global _VERSIONS
    _VERSIONS = None


def detect_tool_versions(config: LauncherConfig) -> ToolVersions:
    """Return the toolchain versions; probes once, then serves the cache.

    Logs the chain as a single INFO line on first probe, analogous to the
    launcher version line, so a future failure report already carries every
    link's version without the user running a single command.
    """
    global _VERSIONS
    if _VERSIONS is not None:
        return _VERSIONS
    _VERSIONS = _probe_versions()
    logger.info("docker toolchain: %s", _VERSIONS.log_line())
    return _VERSIONS


def _probe_versions() -> ToolVersions:
    engine_raw, cli_raw, api_raw = _docker_versions()
    compose_raw = _probe_line(["docker", "compose", "version", "--short"])
    buildx_raw = _probe_line(["docker", "buildx", "version"])
    return ToolVersions(
        engine_raw=engine_raw,
        engine=parse_version(engine_raw),
        cli_raw=cli_raw,
        cli=parse_version(cli_raw),
        api_raw=api_raw,
        api=parse_version(api_raw),
        compose_raw=compose_raw,
        compose=parse_version(compose_raw),
        buildx_raw=buildx_raw,
        buildx=parse_version(buildx_raw),
    )


def _docker_versions() -> tuple[str, str, str]:
    """``(engine, cli, api)`` via one ``docker version --format`` call.

    Tab-separated so a daemon-down run (Server fields empty) still yields the
    client version. Never raises - a missing binary or timeout degrades to
    empty strings.
    """
    fmt = "{{.Server.Version}}\t{{.Client.Version}}\t{{.Server.APIVersion}}"
    try:
        result = _run(["docker", "version", "--format", fmt], timeout=15.0, probe=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "", "", ""
    line = (result.stdout or "").strip().splitlines()
    if not line:
        return "", "", ""
    parts = line[0].split("\t")
    parts += [""] * (3 - len(parts))
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _probe_line(cmd: list[str]) -> str:
    """First output line of ``cmd``, or ``""``. Never raises (probe-quiet)."""
    try:
        result = _run(cmd, timeout=15.0, probe=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    out = (result.stdout or result.stderr or "").strip().splitlines()
    return out[0].strip() if out else ""


def intrinsic_buildx_requirement(compose: Version | None) -> Version | None:
    """The launcher's non-negotiable buildx floor for the plugin build path.

    Returns :data:`BUILDX_MIN_VERSION` only when the detected compose plugin
    is new enough to enforce it (>= :data:`COMPOSE_BUILDX_GATE`); otherwise
    ``None`` - an older compose does not delegate the build through the
    buildx-bake gate, so demanding a newer buildx would block a build that
    would actually succeed.
    """
    if compose is None:
        return None
    return BUILDX_MIN_VERSION if compose >= COMPOSE_BUILDX_GATE else None
