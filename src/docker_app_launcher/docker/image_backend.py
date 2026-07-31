"""Image deployment mode: run a PREBUILT image via the engine API (#78).

The consumer publishes a finished (multi-arch) image; the user machine
needs neither compose nor buildx — pull + run work on old Docker
generations where the build toolchain matrix does not. Approved in
adaptive-learner#2110 (option A1).

Order of acquisition: a configured AND present local image archive
(``docker save`` format) is loaded via the API — the registry-free path;
otherwise the image is pulled with layer progress into the log panel.
Pulling happens on install and on an explicit start only, never silently
in the background. If the registry is unreachable but the image exists
locally, the start proceeds on the local image (offline-capable by
design); if it is missing locally, the failure names the network cause
up front instead of a raw library error.

Registry credentials follow #77: not resolved by default
(``use_registry_credentials`` opts in). Multi-arch: the ENGINE resolves
the platform variant during pull — same ``/images/create`` endpoint the
CLI uses; a missing variant surfaces as the engine's "no matching
manifest" error, which is classified into a clear message here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import py_client
from docker_app_launcher.docker.command_runner import OutputFn, ProgressPctFn, _progress
from docker_app_launcher.docker.dockerfile_backend import (
    _classified_detail,
    _disable_registry_auth,
    _remove_existing,
    port_binding,
)
from docker_app_launcher.launcher_settings import resolve_port

logger = logging.getLogger("docker_app_launcher.docker.image_backend")


def image_present(config: LauncherConfig) -> bool:
    """Whether ``image_reference`` already exists in the local engine."""
    try:
        client = py_client.get_client()
    except Exception:  # noqa: BLE001 - presence probe only
        return False
    try:
        client.images.get(config.image_reference)
        return True
    except Exception:  # noqa: BLE001 - NotFound or API hiccup: treat as absent
        return False
    finally:
        _close(client)


def acquisition_source(config: LauncherConfig) -> str:
    """``"archive"`` or ``"registry"`` - the source :func:`up` would use NOW.

    The same rule as ``_acquire_image``: a configured AND present archive
    wins. Note for the manifest (#80): a start that fell back to a local
    image because the registry was unreachable still records ``registry`` -
    the CONFIGURED source; the recorded image id/digest always reflect the
    image actually present in the engine.
    """
    archive = config.image_archive_path
    return "archive" if (archive is not None and archive.is_file()) else "registry"


def image_identity(config: LauncherConfig) -> dict[str, Any]:
    """Best-effort identity of the configured image for the manifest (#80).

    Returns ``image_reference`` / ``image_id`` / ``image_digests`` /
    ``image_source``; identity fields are omitted (never guessed) when the
    engine or the image is unavailable - the manifest write must stay
    fail-open.
    """
    if not config.image_reference:
        return {}
    identity: dict[str, Any] = {
        "image_reference": config.image_reference,
        "image_source": acquisition_source(config),
    }
    try:
        client = py_client.get_client()
    except Exception:  # noqa: BLE001 - identity probe only
        return identity
    try:
        image = client.images.get(config.image_reference)
        identity["image_id"] = str(getattr(image, "id", "") or "")
        identity["image_digests"] = list(image.attrs.get("RepoDigests", []) or [])
    except Exception:  # noqa: BLE001 - image absent: reference+source still recorded
        pass
    finally:
        _close(client)
    return identity


def up(
    config: LauncherConfig,
    *,
    on_output: OutputFn | None = None,
    on_progress: ProgressPctFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Acquire the image (archive > pull > local fallback) and (re)start it."""
    try:
        client = py_client.get_client()
    except Exception as exc:  # noqa: BLE001 - classified, never a raw traceback
        return 1, _classified_detail(exc, config)
    if not config.use_registry_credentials:
        _disable_registry_auth(client)
    try:
        rc, detail = _acquire_image(
            client, config, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
        if rc != 0:
            return rc, detail
        return _run_pulled_container(client, config)
    except Exception as exc:  # noqa: BLE001 - classified, never a raw traceback
        return 1, _classify_pull_error(exc, config)
    finally:
        _close(client)


def _acquire_image(
    client: Any,
    config: LauncherConfig,
    *,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    archive = config.image_archive_path
    if archive is not None and archive.is_file():
        # Registry-free path wins when configured and present (#78).
        logger.info("loading image archive %s (registry-free path)", archive)
        if on_output is not None:
            on_output(f"loading image archive: {archive.name}")
        with archive.open("rb") as fh:
            client.images.load(fh)
        if not _image_in_local_engine(client, config):
            # The archive is only useful if it actually contains the tag the
            # container will be started BY - anything else must fail here
            # with the file named, not later as a raw ImageNotFound.
            return 1, (
                f"the archive {archive} does not contain the configured "
                f"image_reference {config.image_reference} - the consumer must ship "
                f"an archive built via 'docker save {config.image_reference}'"
            )
        return 0, ""
    logger.info("pulling image %s", config.image_reference)
    try:
        return _pull_with_progress(
            client, config, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
    except Exception as exc:
        if _looks_like_network_error(exc) and _image_in_local_engine(client, config):
            # Offline with a local copy: start MUST work without net.
            msg = "registry unreachable - using the local image"
            logger.warning("%s (%s)", msg, exc)
            if on_output is not None:
                on_output(msg)
            return 0, ""
        raise


def _pull_with_progress(
    client: Any,
    config: LauncherConfig,
    *,
    on_output: OutputFn | None,
    on_progress: ProgressPctFn | None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Stream the pull; every status line reaches the panel — never a
    silent multi-hundred-MB download."""
    from docker.utils import parse_repository_tag

    repository, tag = parse_repository_tag(config.image_reference)
    last_error = ""
    for chunk in client.api.pull(repository, tag=tag, stream=True, decode=True):
        if should_cancel is not None and should_cancel():
            # A REAL cancel (#98): stop consuming the stream - closing the
            # request makes the daemon abort the remaining downloads. The
            # layers fetched so far stay in the local cache ON PURPOSE: they
            # are not damaged, and the next attempt reuses them - kept as a
            # decision, not as a side effect.
            logger.info("pull of %s cancelled by request; fetched layers stay cached", config.image_reference)
            return 1, (
                "cancelled by request - already downloaded layers stay in the local cache and speed up the next attempt"
            )
        if "error" in chunk:
            last_error = str(chunk.get("errorDetail", {}).get("message") or chunk["error"])
            continue
        status = str(chunk.get("status", "")).strip()
        if not status:
            continue
        layer = str(chunk.get("id", "")).strip()
        line = f"{layer}: {status}" if layer else status
        if on_output is not None:
            on_output(line)
        _progress(on_progress, None, line)
    if last_error:
        return 1, _classify_pull_message(last_error, config)
    return 0, ""


def _run_pulled_container(client: Any, config: LauncherConfig) -> tuple[int, str]:
    """(Re)create the container from the pulled/loaded image — same port/
    volume/env/restart block the dockerfile mode uses."""
    _remove_existing(client, config.container_name)
    host_port = resolve_port(config)
    container_port = config.container_port or host_port
    logger.info(
        "image-mode run: name=%s image=%s ports={%s:%s} restart=%s",
        config.container_name,
        config.image_reference,
        host_port,
        container_port,
        config.restart_policy,
    )
    client.containers.run(
        config.image_reference,
        name=config.container_name,
        detach=True,
        ports={f"{container_port}/tcp": port_binding(config, host_port)},
        volumes={name: {"bind": mount, "mode": "rw"} for name, mount in config.container_volumes.items()},
        environment=dict(config.container_env),
        restart_policy={"Name": config.restart_policy} if config.restart_policy else None,
    )
    return 0, ""


def _image_in_local_engine(client: Any, config: LauncherConfig) -> bool:
    try:
        client.images.get(config.image_reference)
        return True
    except Exception:  # noqa: BLE001
        return False


def _looks_like_network_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("timeout", "temporary failure", "no such host", "connection refused", "network"))


def _classify_pull_error(exc: Exception, config: LauncherConfig) -> str:
    return _classify_pull_message(str(exc), config)


def _classify_pull_message(message: str, config: LauncherConfig) -> str:
    """Engine pull errors into actionable text — never a raw library line."""
    lower = message.lower()
    if "no matching manifest" in lower or "does not match the specified platform" in lower:
        # Multi-arch gap: the engine found the image but no variant for
        # this platform (#78) - the consumer must publish it.
        return (
            f"the image {config.image_reference} has no variant for this machine's platform: {message}. "
            "The publisher must provide a multi-arch image (e.g. linux/amd64 + linux/arm64)."
        )
    if any(m in lower for m in ("no such host", "temporary failure", "timeout", "connection refused")):
        return (
            f"could not reach the registry for {config.image_reference}: {message}. "
            "Downloading the app image needs an internet connection; once pulled, the app runs offline."
        )
    refusal_markers = (
        "denied",
        "unauthorized",
        "authentication required",
        "pull access denied",
        "repository does not exist",
    )
    if any(m in lower for m in refusal_markers):
        # Registry token flow refused the pull (#87) - GHCR answers like this
        # for missing AND private repositories. Name the registry access as
        # the cause, never the raw library line.
        return (
            f"the registry refused access to {config.image_reference}: {message}. "
            "Either the image is not published (the publisher must provide a public image at "
            "this reference) or it is private - a private registry needs "
            "use_registry_credentials: true in the launcher config plus a docker login."
        )
    return message


def _close(client: Any) -> None:
    try:
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("client close failed: %s", exc)
