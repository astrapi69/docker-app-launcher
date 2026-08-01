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
from collections.abc import Callable
from typing import Any

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import py_client
from docker_app_launcher.docker.command_runner import OutputFn, ProgressPctFn, _progress
from docker_app_launcher.launcher_settings import resolve_port

logger = logging.getLogger("docker_app_launcher.docker.dockerfile_backend")


class _NoRegistryAuth(dict):  # type: ignore[type-arg]
    """Credential-free stand-in for docker-py's AuthConfig (#77).

    ``get_all_credentials`` is where a stale ``credsStore`` (e.g. a leftover
    ``docker-credential-gcloud``) hard-fails with StoreError - the CLI is
    tolerant here, the SDK is not (recorded error class: switching from CLI
    to SDK inherits different config behavior). Local builds of public base
    images need no registry login, so the resolution must not even start.
    Deliberately NOT empty: docker-py reloads ``~/.docker/config.json`` when
    ``_auth_configs`` is falsy or ``is_empty`` - which would re-arm the
    broken store.

    Shaped as a real ``{"auths": {}}`` dict because the PULL path (#78) does
    not call ``get_all_credentials``: docker-py wraps ``_auth_configs`` in
    its ``AuthConfig`` (itself a dict subclass) and reads only the keys - no
    ``credsStore`` key means no helper lookup, and the empty ``auths`` map
    resolves to an anonymous pull. A non-dict object breaks there with
    "argument of type '_NoRegistryAuth' is not iterable" (found by the #78
    live proof, invisible to the mocked suite).
    """

    is_empty = False

    def __init__(self) -> None:
        super().__init__({"auths": {}})

    def get_all_credentials(self) -> dict[str, Any]:
        return {}


def _disable_registry_auth(client: Any) -> None:
    """Replace the client's filesystem auth config for OUR build call only.

    Duck-typed against docker-py's private ``_auth_configs``; if the
    attribute shape ever changes, the StoreError classification in
    :func:`_classified_detail` still yields an actionable message.
    """
    try:
        client.api._auth_configs = _NoRegistryAuth()
    except Exception as exc:  # noqa: BLE001 - best-effort, fallback classification covers us
        logger.debug("could not neutralize registry auth: %s", exc)


def _mask_url_credentials(url: str) -> str:
    """``http://user:pass@host`` -> ``http://user:***@host`` (log-safe)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
        netloc = f"{parts.username}:***@{parts.hostname}"
        if parts.port:
            netloc += f":{parts.port}"
        return parts._replace(netloc=netloc).geturl()
    except (ValueError, AttributeError):
        return "<unparsable proxy url>"


def _log_proxy_settings(client: Any) -> None:
    """User-config proxies DO flow into the build (docker-py injects them as
    build args, ``use_config_proxy=True`` default) - keep that default, but
    say so in the log (#77): silently inheriting a foreign proxy is a
    surprise source. Values are NEVER logged; a credentialed proxy URL
    additionally gets a masked warning, because with the classic builder
    build args (and thus the credentials) end up in the image history.
    Masking the URL before the build would break authenticated proxies, so
    pass-through + warning is the deliberate choice.
    """
    try:
        proxies = client.api._proxy_configs.get_environment()
        if not proxies:
            return
        logger.info("proxy settings from the docker client config apply to this build: %s", ", ".join(proxies))
        for name, value in proxies.items():
            masked = _mask_url_credentials(str(value))
            if masked != str(value):
                logger.warning(
                    "proxy variable %s contains credentials (%s). With the classic builder, build "
                    "args end up in the image history - prefer a credential-free proxy URL.",
                    name,
                    masked,
                )
    except Exception as exc:  # noqa: BLE001 - log-only helper
        logger.debug("proxy config not readable: %s", exc)


def up(
    config: LauncherConfig,
    *,
    on_output: OutputFn | None = None,
    on_progress: ProgressPctFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
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
        return 1, _classified_detail(exc, config)
    if config.use_registry_credentials:
        # Consumer explicitly declared private registries (#77): resolution
        # runs, and a broken helper is then a HARD error (classified below).
        logger.info("registry credential resolution ENABLED by config (use_registry_credentials)")
    else:
        _disable_registry_auth(client)
    _log_proxy_settings(client)
    try:
        rc, detail = _build(client, config, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel)
        if rc != 0:
            return rc, detail
        return _run_container(client, config)
    except Exception as exc:  # noqa: BLE001 - classified below, never a raw traceback
        return 1, _classified_detail(exc, config)
    finally:
        _close(client)


def recreate(config: LauncherConfig) -> tuple[int, str]:
    """Recreate the container from the ALREADY built image: ``(rc, detail)``.

    The no-rebuild half of :func:`up`, for changes that only affect how the
    container is CREATED (the published host port, #112). The image is
    untouched, so this takes seconds instead of the minutes a rebuild costs -
    the same trade the compose path makes with ``up -d`` instead of
    ``up --build -d``. Publishing goes through :func:`_run_container`, so the
    bind address cannot drift away from install/start (#111).
    """
    try:
        client = py_client.get_client()
    except Exception as exc:  # noqa: BLE001 - classified, never a raw traceback
        return 1, _classified_detail(exc, config)
    try:
        return _run_container(client, config)
    except Exception as exc:  # noqa: BLE001 - classified, never a raw traceback
        return 1, _classified_detail(exc, config)
    finally:
        _close(client)


def _build(
    client: Any,
    config: LauncherConfig,
    *,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
    should_cancel: Callable[[], bool] | None = None,
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
        if should_cancel is not None and should_cancel():
            # Honest cancel (#98): we stop consuming the stream; the classic
            # builder may FINISH the currently running build step in the
            # background before the daemon notices the closed request. The
            # build cache produced so far stays and speeds up the next build
            # (a decision, not a side effect) - no image is tagged, no
            # container is started.
            logger.info("dockerfile build of %s cancelled by request; build cache stays", config.image_name)
            return 1, (
                "cancelled by request - the running build step may still finish in the "
                "background; the build cache stays and speeds up the next attempt"
            )
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
        ports={f"{container_port}/tcp": port_binding(config, host_port)},
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


# The one place that decides WHICH interface a published port binds to (#111).
# Both API-driven modes (image, dockerfile) route through it, so the rule cannot
# drift apart between them.
#
# Measured, not derived: docker-py's bare-int form
# ``ports={"80/tcp": 8080}`` publishes on 0.0.0.0 AND :: - every interface -
# while the docs promised localhost. An app without authentication is then
# reachable from the whole network. The tuple form pins the interface.
LOCALHOST_BIND = "127.0.0.1"
OPEN_BINDS = ("0.0.0.0", "::", "*")


def port_binding(config: LauncherConfig, host_port: int) -> tuple[str, int] | int:
    """``(interface, port)`` for docker-py, warning loudly when it opens up.

    The warning sits HERE, at the moment of opening, rather than on a
    reference page nobody reads while typing a config value.
    """
    address = (config.bind_address or LOCALHOST_BIND).strip()
    if address in OPEN_BINDS:
        logger.warning(
            "publishing %s on %s: the app is reachable from EVERY network this "
            "machine is on, not just from this computer. Anyone who can reach it "
            "can use it - the launcher cannot add authentication the app does not "
            "have. Set bind_address to 127.0.0.1 to undo this.",
            host_port,
            address,
        )
        return (address, host_port)
    return (address, host_port)


def _classified_detail(exc: Exception, config: LauncherConfig | None = None) -> str:
    """Human detail via the #44 exception classification - never duplicated."""
    # Matching on the class NAME, because the exception is raised deep inside
    # docker-py's auth resolution and importing it here would tie this module
    # to a private path. MEASURED (docker-py 7.2.0, real daemon, #110):
    # client.api.build() with a broken credsStore raises
    # docker.credentials.errors.StoreError UNWRAPPED - this match is correct
    # for the launcher's path. AuthConfig.resolve_authconfig() instead WRAPS it
    # in DockerException, where this match does NOT fire; nothing here takes
    # that path today. tests/docker/test_credential_error_identity.py pins the
    # library's class identity, so a rename or move in an upgrade fails loudly
    # instead of silently degrading to an echoed library error.
    if type(exc).__name__ == "StoreError":
        # A broken credsStore/credHelpers entry in ~/.docker/config.json (#77):
        # name the fix, never just echo the library error.
        if config is not None and config.use_registry_credentials:
            return (
                f"registry credential helper is broken: {exc}. This launcher config declares "
                "use_registry_credentials, so working helpers are required - repair the helper "
                "or the credsStore/credHelpers entry in ~/.docker/config.json."
            )
        return (
            f"registry credential helper is broken: {exc}. The launcher build needs no registry "
            "login - remove the stale credsStore/credHelpers entry from ~/.docker/config.json."
        )
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
        return False, _classified_detail(exc, config)
    try:
        container = client.containers.get(config.container_name)
        raw = container.logs(tail=lines)
        return True, raw.decode("utf-8", errors="replace").strip()
    except Exception as exc:  # noqa: BLE001
        return False, _classified_detail(exc, config)
    finally:
        _close(client)
