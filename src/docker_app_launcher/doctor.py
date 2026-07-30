"""One diagnostic pass over config, Docker, readiness, ports and health (#75).

Bundles every existing preflight into a single report a user runs BEFORE
filing a bug or publishing a wrapper release — and, unlike the build
gates, it also diagnoses an ALREADY-RUNNING stack (#76): a running
container bypasses the gates, so port drift or failing health used to be
invisible behind a plain ``--status: running``.

Since #86 the pass is collected as a structured :class:`DoctorReport`
(stable check ids, ``--json``-renderable); the text report renders FROM
that object, and the GUI renders the same object. Report labels are
technical English on purpose (a diagnostic artifact to paste into bug
reports); every embedded blocker/health message comes from the localized
gates.
"""

from __future__ import annotations

import logging
import platform
import subprocess

from docker_app_launcher import __version__
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.diagnostics_report import (
    CheckResult,
    DoctorReport,
    HealthReport,
    StatusReport,
    SupportBundle,
    visible_env_keys,
)
from docker_app_launcher.docker import build_readiness
from docker_app_launcher.docker.command_runner import _run
from docker_app_launcher.docker.detection import check_docker
from docker_app_launcher.docker.lifecycle import get_state, health_check
from docker_app_launcher.docker.tool_versions import detect_tool_versions
from docker_app_launcher.install_manifest import last_aborted_operation, read_manifest
from docker_app_launcher.launcher_settings import resolve_port

logger = logging.getLogger("docker_app_launcher.doctor")

_OK = "✓"
_FAIL = "✗"
_INFO = "·"

_SYMBOL = {"ok": _OK, "error": _FAIL, "info": _INFO, "warn": _FAIL}


def collect_doctor_report(config: LauncherConfig) -> DoctorReport:
    """The diagnostic pass as data - the single source both renderers use."""
    mode = config.effective_deployment_mode
    report = DoctorReport(app_name=config.app_name, mode=mode)
    checks = report.checks

    # 1. Config identity and files.
    checks.append(CheckResult("config_identity", "info", f"app: {config.app_name} | mode: {mode}"))
    if mode == "image":
        # Image mode needs no build context on disk - install_dir is informational.
        checks.append(CheckResult("install_dir", "info", f"install_dir: {config.install_dir or '(unset)'}"))
    else:
        install_ok = bool(config.install_dir) and config.build_context_path.exists()
        checks.append(
            CheckResult(
                "install_dir",
                "ok" if install_ok else "error",
                f"install_dir: {config.install_dir or '(unset)'}",
            )
        )
    if mode == "compose":
        file_ok = config.compose_path.is_file()
        checks.append(
            CheckResult("compose_file_exists", "ok" if file_ok else "error", f"compose file: {config.compose_path}")
        )
    elif mode == "image":
        archive = config.image_archive_path
        file_ok = bool(config.image_reference) or (archive is not None and archive.is_file())
        source = config.image_reference or (str(archive) if archive else "(none)")
        checks.append(CheckResult("image_source_declared", "ok" if file_ok else "error", f"image source: {source}"))
    else:
        file_ok = config.dockerfile_path.is_file()
        checks.append(
            CheckResult("dockerfile_exists", "ok" if file_ok else "error", f"dockerfile: {config.dockerfile_path}")
        )

    # 2. Daemon - terminal when down (no readiness probing against a dead daemon).
    docker_ok, docker_msg = check_docker()
    checks.append(CheckResult("docker_running", "ok" if docker_ok else "error", f"docker: {docker_msg}"))
    if not docker_ok:
        report.complete = False
        return report

    # 3. Toolchain versions (one chain line, same source as the build log).
    try:
        tv = detect_tool_versions(config)
        checks.append(
            CheckResult(
                "toolchain_versions",
                "info",
                f"toolchain: engine={tv.engine or '-'} api={tv.api or '-'} "
                f"compose={tv.compose or '-'} buildx={tv.buildx or '-'}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must not crash on a probe
        checks.append(CheckResult("toolchain_versions", "info", f"toolchain: unreadable ({exc})"))

    # 4. Build readiness (collected blockers, incl. the rendered-port check).
    if mode == "compose":
        blockers = build_readiness.compose_blockers(config)
    elif mode == "image":
        blockers = build_readiness.image_blockers(config)
    else:
        blockers = build_readiness.dockerfile_blockers(config)
    if blockers:
        checks.extend(CheckResult("readiness_blocker", "error", f"readiness: {b}") for b in blockers)
    else:
        checks.append(CheckResult("readiness", "ok", "readiness: ready to build"))

    # 5. Ports and health, INCLUDING the already-running case (#76).
    port = resolve_port(config)
    checks.append(CheckResult("launcher_port", "info", f"launcher port: {port} (env_port_key: {config.env_port_key})"))
    state = get_state(config)
    checks.append(CheckResult("state", "info", f"state: {state}"))
    aborted = last_aborted_operation(config)
    if aborted:
        checks.append(
            CheckResult(
                "last_operation_aborted",
                "info",
                f"last operation: {aborted.get('action', '?')} ended {aborted.get('outcome', '?')} "
                f"at {aborted.get('at', '?')} - include this in a bug report",
            )
        )
    if state == "running":
        published = _published_ports_of_running(config)
        if published:
            checks.append(CheckResult("published_ports", "info", f"published (docker): {published}"))
            if str(port) not in published:
                checks.append(
                    CheckResult(
                        "port_drift",
                        "error",
                        f"port drift: the launcher expects {port} but the running "
                        f"container publishes {published} - check env_port_key/.env",
                    )
                )
        healthy, health_msg = health_check(config)
        url = f"http://localhost:{port}{config.health_check_path}"
        checks.append(CheckResult("health_reachable", "ok" if healthy else "error", f"health: {url} -> {health_msg}"))
    return report


def render_doctor_text(report: DoctorReport) -> str:
    """One line per finding, verdict last - byte-compatible with the pre-#86
    text report, so pasted diagnoses stay comparable across versions."""
    lines = [f"{_SYMBOL[c.status]} {c.message}" for c in report.checks]
    if report.complete:
        verdict = _OK if report.ok else _FAIL
        lines.append(f"{verdict} doctor: {report.problems} problem(s) found")
    return "\n".join(lines)


def run_doctor(config: LauncherConfig) -> tuple[bool, str]:
    """Return ``(healthy, report_text)`` - collection and rendering split (#86)."""
    report = collect_doctor_report(config)
    return report.ok and report.complete, render_doctor_text(report)


def collect_status_report(config: LauncherConfig) -> StatusReport:
    """State + port + health as one object - running-but-broken is visible."""
    port = resolve_port(config)
    state = get_state(config)
    url = f"http://localhost:{port}{config.health_check_path}"
    health_ok: bool | None = None
    health_detail = ""
    if state == "running":
        health_ok, health_detail = health_check(config)
    return StatusReport(
        app_name=config.app_name,
        mode=config.effective_deployment_mode,
        state=state,
        port=port,
        url=url,
        health_ok=health_ok,
        health_detail=health_detail,
    )


def collect_health_report(config: LauncherConfig) -> HealthReport:
    ok, detail = health_check(config)
    port = resolve_port(config)
    return HealthReport(ok=ok, detail=detail, url=f"http://localhost:{port}{config.health_check_path}")


def collect_support_bundle(config: LauncherConfig) -> SupportBundle:
    """The sanitized, human-readable diagnosis document (#86).

    Image identity comes FROM the install manifest (#80) - the bundle never
    probes the engine for it. Env values are never included; key names are
    listed, secret-looking names withheld.
    """
    fields: dict[str, object] = {
        "launcher_version": __version__,
        "app": config.app_name,
        "deployment_mode": config.effective_deployment_mode,
        "os": f"{platform.system()} {platform.release()}",
    }
    docker_ok, docker_msg = check_docker()
    fields["docker"] = docker_msg
    if docker_ok:
        try:
            tv = detect_tool_versions(config)
            fields["toolchain"] = (
                f"engine={tv.engine or '-'} api={tv.api or '-'} compose={tv.compose or '-'} buildx={tv.buildx or '-'}"
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must not crash on a probe
            fields["toolchain"] = f"unreadable ({exc})"
        fields["state"] = get_state(config)
    port = resolve_port(config)
    fields["port"] = port
    fields["env_port_key"] = config.env_port_key
    if fields.get("state") == "running":
        healthy, detail = health_check(config)
        fields["health"] = f"{'ok' if healthy else 'FAILED'} ({detail})"
    manifest = read_manifest(config) or {}
    aborted = last_aborted_operation(config)
    if aborted:
        fields["last_operation"] = (
            f"{aborted.get('action', '?')} ended {aborted.get('outcome', '?')} at {aborted.get('at', '?')}"
        )
    for key in ("image_reference", "image_id", "image_digests", "image_source"):
        if manifest.get(key):
            fields[key] = manifest[key]
    fields["config_dir"] = config.config_dir
    fields["install_dir"] = config.install_dir or "(unset)"
    if config.effective_deployment_mode == "compose":
        fields["compose_file"] = f"{config.compose_path} ({'exists' if config.compose_path.is_file() else 'MISSING'})"
    keys = visible_env_keys(config.container_env)
    withheld = len(config.container_env) - len(keys)
    if config.container_env:
        listed = ", ".join(sorted(keys)) if keys else "(none listable)"
        suffix = f" (+{withheld} secret-looking name(s) withheld)" if withheld else ""
        fields["container_env_keys"] = f"{listed}{suffix} - values are never included"
    return SupportBundle(fields=fields)


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
