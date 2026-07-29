"""One diagnostic pass over config, Docker, readiness, ports and health (#75).

Bundles every existing preflight into a single report a user runs BEFORE
filing a bug or publishing a wrapper release — and, unlike the build
gates, it also diagnoses an ALREADY-RUNNING stack (#76): a running
container bypasses the gates, so port drift or failing health used to be
invisible behind a plain ``--status: running``.

Report labels are technical English on purpose (a diagnostic artifact to
paste into bug reports); every embedded blocker/health message comes from
the localized gates.
"""

from __future__ import annotations

import logging
import subprocess

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import build_readiness
from docker_app_launcher.docker.command_runner import _run
from docker_app_launcher.docker.detection import check_docker
from docker_app_launcher.docker.lifecycle import get_state, health_check
from docker_app_launcher.docker.tool_versions import detect_tool_versions
from docker_app_launcher.launcher_settings import resolve_port

logger = logging.getLogger("docker_app_launcher.doctor")

_OK = "✓"
_FAIL = "✗"
_INFO = "·"


def run_doctor(config: LauncherConfig) -> tuple[bool, str]:
    """Return ``(healthy, report)`` — one line per finding, worst first kept
    inline so the report reads top-to-bottom like the checks ran."""
    lines: list[str] = []
    problems = 0

    # 1. Config identity and files.
    lines.append(f"{_INFO} app: {config.app_name} | mode: {config.effective_deployment_mode}")
    install_ok = bool(config.install_dir) and config.build_context_path.exists()
    lines.append(f"{_OK if install_ok else _FAIL} install_dir: {config.install_dir or '(unset)'}")
    if not install_ok:
        problems += 1
    if config.effective_deployment_mode == "compose":
        file_ok = config.compose_path.is_file()
        lines.append(f"{_OK if file_ok else _FAIL} compose file: {config.compose_path}")
    else:
        file_ok = config.dockerfile_path.is_file()
        lines.append(f"{_OK if file_ok else _FAIL} dockerfile: {config.dockerfile_path}")
    if not file_ok:
        problems += 1

    # 2. Daemon.
    docker_ok, docker_msg = check_docker()
    lines.append(f"{_OK if docker_ok else _FAIL} docker: {docker_msg}")
    if not docker_ok:
        problems += 1
        return False, "\n".join(lines)

    # 3. Toolchain versions (one chain line, same source as the build log).
    try:
        tv = detect_tool_versions(config)
        lines.append(
            f"{_INFO} toolchain: engine={tv.engine or '-'} api={tv.api or '-'} "
            f"compose={tv.compose or '-'} buildx={tv.buildx or '-'}"
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must not crash on a probe
        lines.append(f"{_INFO} toolchain: unreadable ({exc})")

    # 4. Build readiness (collected blockers, incl. the rendered-port check).
    if config.effective_deployment_mode == "compose":
        blockers = build_readiness.compose_blockers(config)
    else:
        blockers = build_readiness.dockerfile_blockers(config)
    if blockers:
        problems += len(blockers)
        lines.extend(f"{_FAIL} readiness: {b}" for b in blockers)
    else:
        lines.append(f"{_OK} readiness: ready to build")

    # 5. Ports and health, INCLUDING the already-running case (#76).
    port = resolve_port(config)
    lines.append(f"{_INFO} launcher port: {port} (env_port_key: {config.env_port_key})")
    state = get_state(config)
    lines.append(f"{_INFO} state: {state}")
    if state == "running":
        published = _published_ports_of_running(config)
        if published:
            lines.append(f"{_INFO} published (docker): {published}")
            if str(port) not in published:
                problems += 1
                lines.append(
                    f"{_FAIL} port drift: the launcher expects {port} but the running "
                    f"container publishes {published} - check env_port_key/.env"
                )
        healthy, health_msg = health_check(config)
        url = f"http://localhost:{port}{config.health_check_path}"
        lines.append(f"{_OK if healthy else _FAIL} health: {url} -> {health_msg}")
        if not healthy:
            problems += 1

    verdict_ok = problems == 0
    lines.append(f"{_OK if verdict_ok else _FAIL} doctor: {problems} problem(s) found")
    return verdict_ok, "\n".join(lines)


def _published_ports_of_running(config: LauncherConfig) -> str:
    """Best-effort ``docker ps`` port column for this project's containers."""
    try:
        result = _run(
            ["docker", "ps", "--filter", f"name={config.container_name}", "--format", "{{.Ports}}"],
            timeout=15.0,
            probe=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return " ".join((result.stdout or "").split())
