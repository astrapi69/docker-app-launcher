"""Manage the app's Docker stack: install, start, stop, uninstall, health.

The verified state machine around Docker Compose: every action returns
``(ok, message)`` and VERIFIES the result instead of assuming success.
Also owns ``get_state`` (the one source of truth the window renders) and
the in-place host/internal port changes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from typing import Any

from docker_app_launcher import __version__
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import build_readiness, dockerfile_backend, image_backend, py_client
from docker_app_launcher.docker.command_runner import (
    BuildCancelled,
    DockerBuildProgress,
    OutputFn,
    ProgressFn,
    ProgressPctFn,
    _docker_op,
    _first_line,
    _notify,
    _progress,
    _run,
    _step_label,
    _stream_command,
    _t,
)
from docker_app_launcher.docker.compose_runtime import compose_available, compose_base_args
from docker_app_launcher.docker.detection import check_docker
from docker_app_launcher.docker.inventory import _project_container_ids, _project_containers, _project_images
from docker_app_launcher.install_manifest import _record_manifest, mark_uninstalled, read_manifest
from docker_app_launcher.launcher_settings import (
    _compose_cwd,
    _validate_internal_port,
    _validate_port,
    _write_env_ports,
    check_port,
    resolve_port,
    set_internal_port,
    set_port,
)

logger = logging.getLogger("docker_app_launcher.docker.lifecycle")

# Predicate polled during a build; True asks the build to stop (#60).
CancelFn = Callable[[], bool]

# Strong DNS/connectivity markers in build output: the first build pulls base
# images, so no network reads as one of these (G5, #59).
_NETWORK_FAILURE_MARKERS = (
    "failed to resolve",
    "no such host",
    "temporary failure in name resolution",
    "could not resolve",
    "network is unreachable",
    "connection refused",
    "i/o timeout",
    "tls handshake timeout",
    "dial tcp",
)


def _looks_like_network_failure(text: str) -> bool:
    """Whether a failed build's output points at a missing network / DNS (G5)."""
    low = (text or "").lower()
    return any(marker in low for marker in _NETWORK_FAILURE_MARKERS)


def _build_failed(config: LauncherConfig, tail: str) -> tuple[bool, str]:
    """A localized build-failure result, classified as a network failure when
    the output points at it (the app is offline-first, but INSTALL needs the
    network to pull base images) (G5, #59)."""
    key = "build_failed_network" if _looks_like_network_failure(tail) else "build_failed"
    return False, _t(config, key, detail=tail)


def _stream_build_with_progress(
    config: LauncherConfig,
    *args: str,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
    lo: int,
    hi: int,
    timeout: float,
    should_cancel: CancelFn | None = None,
) -> tuple[int, str]:
    """Run a ``build`` / ``up --build`` stream, mapping parsed build steps into
    the ``lo..hi`` percentage band while still forwarding raw lines to ``on_output``."""
    parser = DockerBuildProgress(
        lambda pct, label: _progress(on_progress, lo + pct * (hi - lo) // 100, label),
        estimated_total=config.estimated_build_steps,
    )

    def out(line: str) -> None:
        if on_output is not None:
            on_output(line)
        parser.parse_line(line)

    return _stream_compose(config, *args, on_output=out, timeout=timeout, should_cancel=should_cancel)


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
    if config.effective_deployment_mode != "compose":
        # Compose-only by nature, and it says so FIRST (#112): the internal port
        # is carried by env keys the compose file reads. The other modes have no
        # such file, so the old code fell into the same compose detour the host-
        # port change did; an empty key map would answer 'unknown internal port',
        # which names the wrong cause.
        return False, _t(config, "internal_port_compose_only", mode=config.effective_deployment_mode)
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
        # An internal-port change forces an image REBUILD, so the full build
        # capability gate applies (buildx included), not the light guard (#54).
        build_error = _ensure_build_ready(config)
        if build_error is not None:
            return build_error
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
        # --force-recreate: an internal-port change only takes effect on a NEW
        # container; compose would otherwise restart the old one (same reason as
        # start(), where this was measured).
        rc, tail = _stream_compose(
            config, "up", "--build", "-d", "--force-recreate", on_output=on_output, timeout=float(config.build_timeout)
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

    Step 2 is per MODE (#112): compose recreates through its file, the two
    API-driven modes recreate the single container through the engine API.
    ``change_port`` used to take the compose path unconditionally, so image and
    dockerfile mode failed on a compose file the app never had - measured
    against a real daemon, the port was persisted while the running container
    kept the old one.

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
        # BEFORE the stop, not after: a gate that fires later leaves the user
        # with a stopped app and a persisted port they cannot serve.
        recreate_error = _ensure_recreate_ready(config)
        if recreate_error is not None:
            return recreate_error
        stopped, stop_msg = stop(config)
        if not stopped:
            return False, stop_msg

    ok, msg = set_port(config, port)
    if not ok:
        return False, msg
    if not was_running:
        return True, msg

    _notify(on_step, _t(config, "port_restarting"))
    recreated = _recreate_for_port(config, on_output=on_output)
    if recreated is not None:
        return recreated
    if get_state(config) != "running":
        return False, _t(config, "start_no_container")

    _notify(on_step, _t(config, "checking_health"))
    healthy, detail = health_check(config, port)
    if not healthy:
        return False, _t(config, "not_reachable", detail=detail)
    _record_manifest(config, port, action="port_change")
    return True, _t(config, "port_changed", port=port)


def _compose_args(config: LauncherConfig, *args: str) -> list[str]:
    """Build a compose invocation through the DETECTED frontend (#48).

    ``docker compose`` (plugin) or ``docker-compose`` (accepted legacy v1) -
    both support ``-p``/``-f``. Callers guard with :func:`_ensure_compose`
    first, so this never constructs a command for a machine that cannot
    run it.
    """
    return [
        *compose_base_args(config),
        "-p",
        config.compose_project,
        "-f",
        str(config.compose_path),
        *args,
    ]


def _dockerfile_up(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str] | None:
    """Dockerfile-mode build+run (#51): ``None`` on success, else the result.

    Preconditions are checked up front by :func:`_ensure_dockerfile_ready`
    (docker-py importable, Dockerfile present and readable, build context
    resolvable, app-declared engine/API floor) - collected before the build,
    not discovered during it (#54).
    """
    _notify(on_step, _t(config, "install_needs_network"))  # first build pulls base images (G5)
    _notify(on_step, _t(config, "building"))
    _progress(on_progress, None, _t(config, "building"))
    rc, detail = dockerfile_backend.up(
        config, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
    )
    if rc != 0:
        if should_cancel is not None and should_cancel():
            # A cancel is an OUTCOME, not a failure (#98): say what remains
            # (nothing running, caches kept) and what the next step is.
            return False, _t(config, "operation_cancelled", detail=detail)
        return _build_failed(config, detail)
    _notify(on_step, _t(config, "container_started"))
    _progress(on_progress, 95, _t(config, "container_started"))
    return None


def _image_up(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str] | None:
    """Image-mode acquire+run (#78): ``None`` on success, else the result.

    The network pre-warning fires only when it is TRUE: image absent
    locally and no archive configured. A locally present image (or an
    archive) starts without net.
    """
    archive = config.image_archive_path
    needs_net = (archive is None or not archive.is_file()) and not image_backend.image_present(config)
    if needs_net:
        _notify(on_step, _t(config, "image_needs_network"))
    _notify(on_step, _t(config, "image_acquiring"))
    _progress(on_progress, None, _t(config, "image_acquiring"))
    rc, detail = image_backend.up(config, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel)
    if rc != 0:
        if should_cancel is not None and should_cancel():
            return False, _t(config, "operation_cancelled", detail=detail)
        return False, _t(config, "image_acquire_failed", detail=detail)
    _notify(on_step, _t(config, "container_started"))
    _progress(on_progress, 95, _t(config, "container_started"))
    return None


def _ensure_compose(config: LauncherConfig) -> tuple[bool, str] | None:
    """The light compose guard for NON-build compose ops (logs, host-port
    recreate): ``None`` when compose is usable, else the actionable
    ``(False, message)``. No version/build-capability check - those callers
    do not build, so buildx is irrelevant to them.

    Verified device failure without this guard (#48): the 20.10 CLI
    swallows the unknown word ``compose`` and dies on ``-p`` with the full
    help dump as the "error message".
    """
    usable, verdict = compose_available(config)
    if usable:
        return None
    if verdict == "legacy_incompatible":
        return False, _t(config, "compose_v1_incompatible", path=config.compose_path)
    return False, _t(config, "compose_missing")


def _ensure_build_ready(config: LauncherConfig) -> tuple[bool, str] | None:
    """The SINGLE compose-mode build capability gate (#54).

    ``None`` when the toolchain can actually build this project, else the
    collected, actionable ``(False, message)`` naming EVERY missing/too-old
    link at once (compose file, frontend, buildx and any app-declared floor).
    Runs BEFORE the build - a minutes-long build must never fail on a
    precondition that was knowable up front - and must be called only after
    :func:`check_docker` confirmed the daemon (engine/API versions need it).
    """
    blockers = build_readiness.compose_blockers(config)
    if blockers:
        return False, build_readiness.join_blockers(blockers)
    return None


def _ensure_image_ready(config: LauncherConfig) -> tuple[bool, str] | None:
    """The image-mode capability gate (#78): collected, actionable."""
    blockers = build_readiness.image_blockers(config)
    if blockers:
        return False, build_readiness.join_blockers(blockers)
    return None


def _ensure_dockerfile_ready(config: LauncherConfig) -> tuple[bool, str] | None:
    """The dockerfile-mode build capability gate (#51/#54): capability, not
    existence. ``None`` when ready, else the collected ``(False, message)``."""
    blockers = build_readiness.dockerfile_blockers(config)
    if blockers:
        return False, build_readiness.join_blockers(blockers)
    return None


def _ensure_recreate_ready(config: LauncherConfig) -> tuple[bool, str] | None:
    """The per-mode gate for a NO-BUILD recreate (the host-port change, #112).

    What the recreate really needs, not a proxy for it: compose mode needs a
    usable compose frontend, the two API-driven modes need docker-py - and
    NEITHER needs the build toolchain, because no image is built or acquired
    here. ``None`` when the recreate can run, else the actionable result.
    """
    mode = config.effective_deployment_mode
    if mode == "compose":
        return _ensure_compose(config)
    if py_client.available():
        return None
    key = "image_mode_needs_dockerpy" if mode == "image" else "dockerfile_mode_needs_dockerpy"
    return False, _t(config, key)


def _recreate_for_port(config: LauncherConfig, *, on_output: OutputFn | None) -> tuple[bool, str] | None:
    """Recreate the running stack on the persisted port: ``None`` on success.

    Deliberately NO build and NO image acquisition in any mode - only the
    published host port changed, so this must take seconds. Both API modes go
    through their backend's ``recreate``, which publishes via
    ``port_binding(config, host_port)``; a port change therefore cannot
    republish on a wider interface than install/start did (#111).
    """
    mode = config.effective_deployment_mode
    if mode in ("image", "dockerfile"):
        backend = image_backend if mode == "image" else dockerfile_backend
        rc, detail = backend.recreate(config)
        return None if rc == 0 else (False, _t(config, "start_failed", detail=detail))
    try:
        rc, tail = _stream_compose(config, "up", "-d", on_output=on_output, timeout=float(config.start_timeout))
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "start_timeout")
    return None if rc == 0 else (False, _t(config, "start_failed", detail=tail))


def _stream_compose(
    config: LauncherConfig,
    *args: str,
    on_output: OutputFn | None = None,
    timeout: float,
    should_cancel: CancelFn | None = None,
) -> tuple[int, str]:
    return _stream_command(
        _compose_args(config, *args),
        on_output=on_output,
        timeout=timeout,
        cwd=_compose_cwd(config),
        should_cancel=should_cancel,
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
    on_progress: ProgressPctFn | None = None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str]:
    """Build + start the stack, then VERIFY it is running and healthy.

    Guards (each returns ``(False, ...)``): invalid port, Docker down, missing
    compose file, occupied port. If the app is already running it returns
    ``(True, already_installed)``. Streams the build output through
    ``on_output`` and reports a 0/25/50/health/100 bar via ``on_progress``.
    """
    port = resolve_port(config)
    valid, reason = _validate_port(port)
    if not valid:
        return False, reason

    _call(config, config.on_before_install)
    _notify(on_step, _t(config, "checking_docker"))
    _progress(on_progress, 0, _t(config, "checking_docker"))
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    if get_state(config) == "running":
        return True, _t(config, "already_installed")
    # .env FIRST, then the gate: the rendered-port check inside the gate
    # must see the .env the build will actually use (env write is a cheap,
    # idempotent file update - harmless even when the gate then refuses).
    _write_env_ports(config)
    # ONE capability gate per mode, BEFORE the build: collect every
    # missing/too-old link so the whole chain surfaces in a single run (#54).
    if config.effective_deployment_mode == "compose":
        build_error = _ensure_build_ready(config)
        if build_error is not None:
            return build_error
    elif config.effective_deployment_mode == "image":
        image_error = _ensure_image_ready(config)
        if image_error is not None:
            return image_error
    else:
        dockerfile_error = _ensure_dockerfile_ready(config)
        if dockerfile_error is not None:
            return dockerfile_error
    port_free, _ = check_port(port)
    if not port_free:
        return False, _t(config, "port_occupied", port=port)
    _notify(on_step, _t(config, "docker_ok"))

    if config.effective_deployment_mode == "image":
        image_error = _image_up(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
        if image_error is not None:
            return image_error
        return _verify_install(config, port, on_step=on_step, on_progress=on_progress)
    if config.effective_deployment_mode == "dockerfile":
        dockerfile_error = _dockerfile_up(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
        if dockerfile_error is not None:
            return dockerfile_error
        return _verify_install(config, port, on_step=on_step, on_progress=on_progress)

    _notify(on_step, _t(config, "install_needs_network"))  # first build pulls base images (G5)
    _notify(on_step, _t(config, "building"))
    _progress(on_progress, 5, _t(config, "building"))
    try:
        build_rc, build_tail = _stream_build_with_progress(
            config,
            "build",
            on_output=on_output,
            on_progress=on_progress,
            lo=5,
            hi=85,
            timeout=float(config.build_timeout),
            should_cancel=should_cancel,
        )
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "build_timeout")
    except BuildCancelled:
        return False, _t(config, "build_cancelled")
    if build_rc != 0:
        return _build_failed(config, build_tail)
    _notify(on_step, _t(config, "image_built"))
    _progress(on_progress, 85, _t(config, "image_built"))

    _notify(on_step, _t(config, "starting"))
    _progress(on_progress, 88, _t(config, "starting"))
    try:
        up_rc, up_tail = _stream_compose(config, "up", "-d", on_output=on_output, timeout=float(config.start_timeout))
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "start_timeout")
    if up_rc != 0:
        return False, _t(config, "start_failed", detail=up_tail)
    _notify(on_step, _t(config, "container_started"))

    return _verify_install(config, port, on_step=on_step, on_progress=on_progress)


def _verify_install(
    config: LauncherConfig,
    port: int,
    *,
    on_step: ProgressFn | None,
    on_progress: ProgressPctFn | None,
) -> tuple[bool, str]:
    """Shared install verification: running state + health + manifest."""
    _notify(on_step, _t(config, "checking_health"))
    _progress(on_progress, None, _t(config, "checking_health"))  # indeterminate: duration unknown
    if get_state(config) != "running":
        return False, _t(config, "container_not_running")
    healthy, health_msg = health_check(config)
    if not healthy:
        return False, _t(config, "not_reachable", detail=health_msg)
    _notify(on_step, _t(config, "health_ok"))
    _progress(on_progress, 100, _t(config, "ready"))
    _record_manifest(config, port, action="install")
    _call(config, config.on_after_install)
    return True, _t(config, "ready")


def ensure_installed(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
    on_progress: ProgressPctFn | None = None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str]:
    """Single install entry point for the persistent window.

    For a generic app the compose file must already be present, so this is
    :func:`install`. It exists as a stable seam: an app that ships frozen
    binaries can wire a download step via ``config.on_before_install``.
    """
    return install(config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel)


def start(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
    on_progress: ProgressPctFn | None = None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str]:
    """Start the stack via ``compose up --build -d --force-recreate``, then
    VERIFY it runs.

    Always passes ``--build`` so a code change is picked up on the next start;
    Docker's layer cache makes an unchanged rebuild near-instant. ``--build``
    alone was not enough: compose decides recreation from the service config
    hash, not the image content, so a stopped container was RESTARTED on the
    OLD image while the rebuilt image sat unused - the newly built code never
    ran (measured: the #88 compose rebuild cell served stale content, and this
    is the path :func:`update` re-acquires through). ``--force-recreate`` makes
    the rebuilt image actually take effect, honoring this function's contract.
    Named volumes survive a recreate, so data is preserved. ``up --build -d``
    also creates the containers if they do not exist yet, so it works from both
    'stopped' and a removed state.
    """
    _call(config, config.on_before_start)
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    if get_state(config) == "running":
        return True, _t(config, "already_running")
    # .env FIRST, then the gate - symmetric with install(): the rendered-port
    # check must see the .env the 'up --build' will actually use, not a
    # stale one from before a config port change (review finding 2026-07-28).
    _write_env_ports(config)
    # Build capability gate BEFORE the (re)build - after the daemon check, so
    # the engine/API versions are readable, and only when we are about to
    # build (an already-running stack short-circuited above) (#54).
    if config.effective_deployment_mode == "compose":
        build_error = _ensure_build_ready(config)
        if build_error is not None:
            return build_error
    elif config.effective_deployment_mode == "image":
        image_error = _ensure_image_ready(config)
        if image_error is not None:
            return image_error
    else:
        dockerfile_error = _ensure_dockerfile_ready(config)
        if dockerfile_error is not None:
            return dockerfile_error
    _notify(on_step, _t(config, "updating"))
    _progress(on_progress, 5, _t(config, "updating"))
    if config.effective_deployment_mode == "image":
        image_error = _image_up(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
        if image_error is not None:
            return image_error
    elif config.effective_deployment_mode == "dockerfile":
        dockerfile_error = _dockerfile_up(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
        if dockerfile_error is not None:
            return dockerfile_error
    else:
        try:
            rc, tail = _stream_build_with_progress(
                config,
                "up",
                "--build",
                "-d",
                # Recreate from the freshly built image (see docstring): without
                # this compose may restart the old container on the old image.
                "--force-recreate",
                on_output=on_output,
                on_progress=on_progress,
                lo=5,
                hi=95,
                timeout=float(config.build_timeout),
                should_cancel=should_cancel,
            )
        except FileNotFoundError:
            return False, _t(config, "docker_unavailable")
        except subprocess.TimeoutExpired:
            return False, _t(config, "start_timeout")
        except BuildCancelled:
            return False, _t(config, "build_cancelled")
        if rc != 0:
            return False, _t(config, "start_failed", detail=tail)
    if get_state(config) != "running":
        return False, _t(config, "start_no_container")
    _progress(on_progress, 100, _t(config, "start_done"))
    existing = read_manifest(config) or {}
    _record_manifest(config, int(existing.get("port", resolve_port(config))), action="update")
    _call(config, config.on_after_start)
    return True, _t(config, "start_done")


def _rollback_hint(config: LauncherConfig, previous: dict[str, Any]) -> str:
    """A localized 'how to get back to the working version' line for a failed
    update (#92).

    Image mode names the OUTGOING image so the user can retag it and start
    again - the previous image is still on disk after a re-pull (#88), and its
    identity was recorded in the manifest before the update (#80). The build
    modes overwrite the tag in place and keep no separate handle, so they fall
    back to the generic reinstall hint.
    """
    if config.effective_deployment_mode == "image":
        image_id = str(previous.get("image_id") or "")
        reference = str(previous.get("image_reference") or config.image_reference or "")
        if image_id and reference:
            return _t(config, "update_rollback_image", image_id=image_id, reference=reference)
    return _t(config, "update_rollback_generic")


def update(
    config: LauncherConfig,
    *,
    on_step: ProgressFn | None = None,
    on_output: OutputFn | None = None,
    on_progress: ProgressPctFn | None = None,
    should_cancel: CancelFn | None = None,
) -> tuple[bool, str]:
    """Update the app in ONE step: stop -> re-acquire -> start -> health (#92).

    The missing action the #88 matrix surfaced: from a running stack there was
    no single "update" - ``install`` returned already-installed and ``start``
    returned already-running, so the working path was the two-step Stop then
    Start. This wraps it:

    1. stop the stack (a no-op when already stopped);
    2. re-acquire and start via :func:`start` - image mode re-pulls
       ``image_reference``, the build modes rebuild; ``start`` also refreshes
       the manifest identity (#80) with action ``update``;
    3. health-check the result. On failure, surface the health detail together
       with a rollback hint that names the PREVIOUS image (still local, #88), so
       the user can return to the version that worked.

    Named volumes survive throughout (data is preserved, #88). Returns
    ``(ok, message)``; guards (Docker down, not installed) return
    ``(False, ...)``.
    """
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    if get_state(config) == "not_installed":
        return False, _t(config, "update_not_installed")

    # Snapshot the outgoing identity BEFORE anything changes: start() overwrites
    # the manifest, so the rollback anchor must be captured up front.
    previous = read_manifest(config) or {}

    _notify(on_step, _t(config, "update_stopping"))
    _progress(on_progress, 0, _t(config, "update_stopping"))
    stopped, stop_msg = stop(config)
    if not stopped:
        return False, stop_msg

    _notify(on_step, _t(config, "update_fetching"))
    started, start_msg = start(
        config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
    )
    if not started:
        if should_cancel is not None and should_cancel():
            # The trickiest cancel (#98): the app was STOPPED before the
            # re-acquire, so a cancelled update leaves a state the user did
            # not ask for. Say it, with the next step - the previous image
            # is still local (#88), Start runs it.
            return False, _t(config, "update_cancelled_stopped", detail=start_msg)
        # start() already classified the build/pull failure - pass it through.
        return False, start_msg

    _notify(on_step, _t(config, "checking_health"))
    _progress(on_progress, None, _t(config, "checking_health"))
    healthy, detail = health_check(config)
    if not healthy:
        return False, _t(config, "update_health_failed", detail=detail, hint=_rollback_hint(config, previous))
    _progress(on_progress, 100, _t(config, "update_done"))
    return True, _t(config, "update_done")


def app_logs(config: LauncherConfig, *, lines: int | None = None) -> tuple[bool, str]:
    """Fetch the tail of the app's container logs (P2).

    Returns ``(ok, text)``: the last ``lines`` (default
    ``config.log_tail_lines``) lines of every project container via
    ``docker compose logs``, or ``(False, <localized error>)``. Works for
    running AND stopped containers — a crashed container's last words are
    exactly what the user needs to see.
    """
    docker_ok, _ = check_docker()
    if not docker_ok:
        return False, _t(config, "docker_unavailable")
    if get_state(config) == "not_installed":
        return False, _t(config, "not_installed")
    tail = lines if lines is not None else config.log_tail_lines
    if config.effective_deployment_mode in ("dockerfile", "image"):
        ok, text = dockerfile_backend.tail_logs(config, lines=tail)
        if not ok:
            return False, _t(config, "app_logs_failed", detail=text)
        return True, text or _t(config, "app_logs_empty")
    compose_error = _ensure_compose(config)
    if compose_error is not None:
        return compose_error
    cmd = _compose_args(config, "logs", "--no-color", "--tail", str(tail))
    try:
        result = _run(cmd, timeout=30.0, cwd=_compose_cwd(config))
    except FileNotFoundError:
        return False, _t(config, "docker_unavailable")
    except subprocess.TimeoutExpired:
        return False, _t(config, "app_logs_failed", detail="timed out")
    if result.returncode != 0:
        return False, _t(config, "app_logs_failed", detail=_first_line(result.stderr) or "unknown error")
    text = (result.stdout or "").strip()
    if not text:
        return True, _t(config, "app_logs_empty")
    return True, text


def _get_api_client() -> Any | None:
    """A native API client, or ``None`` when docker-py cannot deliver one.

    Indirection point: the test suite pins this to ``None`` by default so no
    test ever talks to the real daemon socket (#44).
    """
    if not py_client.available():
        return None
    try:
        return py_client.get_client()
    except Exception as exc:  # noqa: BLE001 - unavailable, not fatal
        logger.debug("native API client unavailable: %s", exc)
        return None


def _pump_container_logs(container: Any, on_line: OutputFn, tail: int) -> None:
    """Forward one container's followed log stream to ``on_line``.

    Runs on its own thread; ends when the stream ends — closing the shared
    client from the coordinating thread closes the socket, which ends the
    generator and lets this thread exit.
    """
    prefix = getattr(container, "name", "") or ""
    try:
        for raw in container.logs(stream=True, follow=True, tail=tail):
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            with contextlib.suppress(Exception):
                on_line(f"{prefix} | {line}" if prefix else line)
    except Exception as exc:  # noqa: BLE001 - stream teardown must never propagate
        logger.debug("log stream for %r ended: %s", prefix, exc)


def stream_app_logs(
    config: LauncherConfig,
    *,
    on_line: OutputFn,
    should_stop: Callable[[], bool] | None = None,
    lines: int | None = None,
    poll_interval: float = 0.2,
) -> tuple[bool, str]:
    """Follow the app's container logs live, one ``on_line`` call per line (#44).

    Native-API only (``docker-py``): each project container gets a follower
    thread, lines are prefixed with the container name. Blocks until
    ``should_stop()`` returns True or every stream ends; callers without
    docker-py get ``(False, message)`` and should fall back to the one-shot
    :func:`app_logs`.
    """
    client = _get_api_client()
    if client is None:
        return False, _t(config, "app_logs_failed", detail="docker-py unavailable")
    stop = should_stop or (lambda: False)
    tail = lines if lines is not None else config.log_tail_lines
    try:
        name_filters = list(config.name_filters())
        containers = client.containers.list(all=True, filters={"name": name_filters} if name_filters else None)
        if not containers:
            return False, _t(config, "not_installed")
        followers = []
        for container in containers:
            thread = threading.Thread(
                target=_pump_container_logs, args=(container, on_line, tail), daemon=True, name="dal-log-follow"
            )
            thread.start()
            followers.append(thread)
        while any(thread.is_alive() for thread in followers):
            if stop():
                break
            time.sleep(poll_interval)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - a broken stream is a failed result, not a crash
        return False, _t(config, "app_logs_failed", detail=str(exc))
    finally:
        # Closing the client tears down the log sockets, ending every
        # follower generator - the daemon threads then exit on their own.
        with contextlib.suppress(Exception):
            client.close()


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


def open_url(url: str) -> None:
    """Open an arbitrary URL (e.g. the Docker install guide). Never raises."""
    try:
        webbrowser.open(url)
    except OSError as exc:
        logger.warning("could not open url %s: %s", url, exc)


def get_version(config: LauncherConfig) -> str:
    """Return the recorded app version (manifest), else the launcher version."""
    data = read_manifest(config)
    if data and data.get("app_version"):
        return str(data["app_version"])
    return __version__


def _health_payload(config: LauncherConfig, port: int, timeout: float = 1.5) -> dict[str, Any] | None:
    """The parsed health-endpoint JSON body, or None (fail-open, #35).

    A short timeout keeps a synchronous caller (the About dialog) snappy;
    a stopped stack fails instantly with connection-refused on localhost.
    """
    url = f"http://localhost:{port}{config.health_check_path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # localhost only
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - any failure means no runtime answer
        return None
    return payload if isinstance(payload, dict) else None


def get_app_version(config: LauncherConfig) -> tuple[str, str]:
    """The managed app's version plus its source (#35).

    Ladder, each step failing open to the next:

    1. ("2.6.0", "running") - the running stack's own claim, read from
       app_version_health_key in the health-endpoint JSON. The only
       source that survives out-of-band rebuilds (git pull + compose build).
    2. (.., "installed") - the install manifest's snapshot.
    3. (.., "expected") - config.app_version, what the NEXT install
       would deploy.
    4. ("", "unknown") - nothing known.
    """
    if config.app_version_health_key:
        payload = _health_payload(config, resolve_port(config))
        if payload:
            running = payload.get(config.app_version_health_key)
            if running:
                return str(running), "running"
    manifest = read_manifest(config)
    if manifest and manifest.get("app_version") and manifest.get("status") != "uninstalled":
        return str(manifest["app_version"]), "installed"
    if config.app_version:
        return config.app_version, "expected"
    return "", "unknown"
