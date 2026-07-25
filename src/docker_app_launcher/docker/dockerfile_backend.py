"""Dockerfile deployment mode: build/run directly through docker-py (#51).

Zero compose dependency - this is the path that runs on the Docker-20.10
QA reference device (no compose v2 plugin, #48 forensics). One service,
one container: image built from ``config.dockerfile_path`` in
``config.build_context_path``, run with the ports/volumes/env/restart
policy from the config.

Errors are classified through :mod:`py_client` (#44) - EACCES on the
socket reads as the permission verdict, never as a generic failure.
"""

from __future__ import annotations

import logging
from typing import Any

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import py_client
from docker_app_launcher.docker.command_runner import OutputFn, ProgressPctFn, _progress
from docker_app_launcher.launcher_settings import resolve_port

logger = logging.getLogger("docker_app_launcher.docker.dockerfile_backend")


def up(
    config: LauncherConfig,
    *,
    on_output: OutputFn | None = None,
    on_progress: ProgressPctFn | None = None,
) -> tuple[int, str]:
    """Build the image and (re)start its container: ``(rc, detail)``.

    Mirrors the compose backend's ``up --build -d`` contract: rc 0 on
    success, otherwise a non-zero rc with the failure detail. Build output
    streams line-by-line into ``on_output`` (the log panel) - never a
    silent wait.
    """
    try:
        client = py_client.get_client()
    except Exception as exc:  # noqa: BLE001 - classified below, never a raw traceback
        return 1, _classified_detail(exc)
    try:
        rc, detail = _build(client, config, on_output=on_output, on_progress=on_progress)
        if rc != 0:
            return rc, detail
        return _run_container(client, config)
    except Exception as exc:  # noqa: BLE001 - classified below, never a raw traceback
        return 1, _classified_detail(exc)
    finally:
        _close(client)


def _build(
    client: Any,
    config: LauncherConfig,
    *,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
) -> tuple[int, str]:
    """Stream the docker-py build; forward every line to the log panel."""
    if not config.dockerfile_path.is_file():
        return 1, f"Dockerfile not found: {config.dockerfile_path}"
    logger.info(
        "dockerfile build: context=%s dockerfile=%s tag=%s",
        config.build_context_path,
        config.dockerfile_path,
        config.image_name,
    )
    last_error = ""
    step = 0
    for chunk in client.api.build(
        path=str(config.build_context_path),
        dockerfile=str(config.dockerfile_path),
        tag=config.image_name,
        rm=True,
        decode=True,
    ):
        if "stream" in chunk:
            line = str(chunk["stream"]).rstrip()
            if line:
                step += 1
                if on_output is not None:
                    on_output(line)
                _progress(on_progress, None, line)
        if "errorDetail" in chunk:
            last_error = str(chunk["errorDetail"].get("message", "")).strip() or "build error"
    if last_error:
        return 1, last_error
    return 0, ""


def _run_container(client: Any, config: LauncherConfig) -> tuple[int, str]:
    """(Re)create the single service container from the built image."""
    _remove_existing(client, config.container_name)
    host_port = resolve_port(config)
    container_port = config.container_port or host_port
    logger.info(
        "dockerfile run: name=%s image=%s ports={%s:%s} restart=%s",
        config.container_name,
        config.image_name,
        host_port,
        container_port,
        config.restart_policy,
    )
    client.containers.run(
        config.image_name,
        name=config.container_name,
        detach=True,
        ports={f"{container_port}/tcp": host_port},
        volumes={name: {"bind": mount, "mode": "rw"} for name, mount in config.container_volumes.items()},
        environment=dict(config.container_env),
        restart_policy={"Name": config.restart_policy} if config.restart_policy else None,
    )
    return 0, ""


def _remove_existing(client: Any, name: str) -> None:
    """Remove a leftover container of the same name (stopped or running)."""
    try:
        stale = client.containers.get(name)
    except Exception:  # noqa: BLE001 - not found (or API hiccup): nothing to remove
        return
    logger.info("removing existing container %s before recreate", name)
    stale.remove(force=True)


def _classified_detail(exc: Exception) -> str:
    """Human detail via the #44 exception classification - never duplicated."""
    verdict = py_client._classify_exception(exc)
    if verdict == "permission":
        return "permission denied on the docker socket (not in the 'docker' group)"
    return str(exc) or verdict


def _close(client: Any) -> None:
    try:
        client.close()
    except Exception as exc:  # noqa: BLE001 - teardown must never mask the result
        logger.debug("client close failed: %s", exc)


def tail_logs(config: LauncherConfig, *, lines: int) -> tuple[bool, str]:
    """The last ``lines`` log lines of the single service container."""
    try:
        client = py_client.get_client()
    except Exception as exc:  # noqa: BLE001
        return False, _classified_detail(exc)
    try:
        container = client.containers.get(config.container_name)
        raw = container.logs(tail=lines)
        return True, raw.decode("utf-8", errors="replace").strip()
    except Exception as exc:  # noqa: BLE001
        return False, _classified_detail(exc)
    finally:
        _close(client)
