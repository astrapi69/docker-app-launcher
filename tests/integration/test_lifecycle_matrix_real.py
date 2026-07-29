"""REAL lifecycle matrix (#79): the full operation set per deployment mode.

Launched by ``run_lifecycle_matrix_integration.sh`` against the LOCAL Docker
daemon. For each of the three modes - image, dockerfile, compose - a tiny
fixture app is driven through the COMPLETE operation set and the transitions
between states, because the mocked suite cannot catch cross-layer breaks by
nature (proven twice: the #77 sentinel was correct for the build path and
broke the pull path; the #78 dispatch fell from a successful image acquire
into the compose build):

    install -> install again (already installed) -> logs -> stop ->
    start (restart a STOPPED stack) -> stop -> uninstall ->
    stop/uninstall when nothing runs

The checked set is enumerated in ``_OPERATIONS`` and asserted complete at
the end of every mode run - no silently skipped operation.

Gates: ``DAL_LIFECYCLE_MATRIX=1`` (never runs in the Docker-free suite);
``DAL_LIFECYCLE_MATRIX_MODE`` optionally narrows to one mode. Runtime split
(documented in docs/environment-matrix.md): the per-push CI runs the unit
suite and the old-engine cell; this full matrix runs nightly and on demand.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_LIFECYCLE_MATRIX") != "1",
        reason="real lifecycle matrix: only via run_lifecycle_matrix_integration.sh",
    ),
]

_ONLY = os.environ.get("DAL_LIFECYCLE_MATRIX_MODE", "")

_DOCKERFILE = """\
FROM busybox:1.36.1
WORKDIR /www
COPY index.html /www/
EXPOSE 80
CMD ["busybox", "httpd", "-f", "-p", "80", "-h", "/www"]
"""

_COMPOSE = """\
services:
  app:
    build: .
    container_name: {container}
    ports:
      - "${{APP_PORT:-{port}}}:80"
    volumes:
      - appdata:/data
volumes:
  appdata:
    name: {container}-data
"""

# The operation set every mode must pass - the proof of the checked set.
_OPERATIONS = [
    "install",
    "install_when_installed",
    "app_logs",
    "stop",
    "start_stopped_stack",
    "stop_again",
    "uninstall",
    "stop_when_nothing_runs",
]


# Real fixtures against the real daemon: the conftest isolation fixtures are
# shadowed by these same-named no-ops so detection/inventory/compose/tool
# probes hit the actual system (that is the point of the matrix).
@pytest.fixture(autouse=True)
def _isolate_docker_api() -> None:
    return None


@pytest.fixture(autouse=True)
def _isolate_compose_frontend():
    from docker_app_launcher.docker import compose_runtime

    compose_runtime.reset_compose_cache()
    yield
    compose_runtime.reset_compose_cache()


@pytest.fixture(autouse=True)
def _isolate_tool_versions():
    from docker_app_launcher.docker import tool_versions

    tool_versions.reset_versions_cache()
    yield
    tool_versions.reset_versions_cache()


def _mode_config(mode: str, tmp_path: Path):
    from docker_app_launcher.config import LauncherConfig

    port = {"image": 18131, "dockerfile": 18132, "compose": 18133}[mode]
    container = f"dal-matrix-{mode}"
    install_dir = tmp_path / mode
    install_dir.mkdir(parents=True, exist_ok=True)
    if mode in ("dockerfile", "compose"):
        (install_dir / "index.html").write_text('{"status": "ok"}\n', encoding="utf-8")
        (install_dir / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    if mode == "compose":
        (install_dir / "docker-compose.prod.yml").write_text(
            _COMPOSE.format(container=container, port=port), encoding="utf-8"
        )
    return LauncherConfig(
        app_name=f"Matrix {mode}",
        container_name=container,
        compose_project=container,
        image_name=container,
        deployment_mode=mode,
        image_reference="traefik/whoami:v1.10" if mode == "image" else "",
        # The named volume is the update-path proof object (#88): compose
        # mounts it via the yaml above (pinned name), the API modes here.
        container_volumes={} if mode == "compose" else {f"{container}-data": "/data"},
        install_dir=str(install_dir),
        config_dir=str(tmp_path / f".{container}"),
        default_port=port,
        # image/dockerfile publish host_port -> 80 (the fixtures listen on 80);
        # compose maps the port in the yaml itself.
        container_port=80 if mode in ("image", "dockerfile") else 0,
        health_check_path="/",
        health_check_key="",  # HTTP 200 is the contract for the fixtures
        health_check_timeout=60,
        locale="en",
    ).resolve()


def _drive_full_operation_set(mode: str, tmp_path: Path) -> None:
    from docker_app_launcher.docker import lifecycle

    config = _mode_config(mode, tmp_path)
    checked: list[str] = []
    lifecycle.uninstall(config)  # clean slate, best effort

    ok, msg = lifecycle.install(config)
    assert ok, f"[{mode}] install failed: {msg}"
    assert lifecycle.get_state(config) == "running", f"[{mode}] not running after install"
    checked.append("install")

    ok, msg = lifecycle.install(config)
    assert ok, f"[{mode}] install-when-installed must be graceful: {msg}"
    checked.append("install_when_installed")

    ok, text = lifecycle.app_logs(config)
    assert ok, f"[{mode}] app_logs failed: {text}"
    checked.append("app_logs")

    ok, msg = lifecycle.stop(config)
    assert ok, f"[{mode}] stop failed: {msg}"
    assert lifecycle.get_state(config) == "stopped", f"[{mode}] not stopped after stop"
    checked.append("stop")

    ok, msg = lifecycle.start(config)
    assert ok, f"[{mode}] restarting the stopped stack failed: {msg}"
    assert lifecycle.get_state(config) == "running", f"[{mode}] not running after restart"
    checked.append("start_stopped_stack")

    ok, msg = lifecycle.stop(config)
    assert ok, f"[{mode}] second stop failed: {msg}"
    checked.append("stop_again")

    ok, msg = lifecycle.uninstall(config)
    assert ok, f"[{mode}] uninstall failed: {msg}"
    assert lifecycle.get_state(config) == "not_installed", f"[{mode}] leftovers after uninstall"
    checked.append("uninstall")

    ok, msg = lifecycle.stop(config)
    assert not ok, f"[{mode}] stop with nothing running must say so, got ok: {msg}"
    checked.append("stop_when_nothing_runs")

    assert checked == _OPERATIONS, f"[{mode}] operation set incomplete: {checked}"


def _skip_unless(mode: str) -> None:
    if _ONLY and mode != _ONLY:
        pytest.skip(f"DAL_LIFECYCLE_MATRIX_MODE={_ONLY} narrows this run")


class TestImageModeLifecycle:
    def test_full_operation_set(self, tmp_path: Path) -> None:
        _skip_unless("image")
        _drive_full_operation_set("image", tmp_path)


class TestDockerfileModeLifecycle:
    def test_full_operation_set(self, tmp_path: Path) -> None:
        _skip_unless("dockerfile")
        _drive_full_operation_set("dockerfile", tmp_path)


class TestComposeModeLifecycle:
    def test_full_operation_set(self, tmp_path: Path) -> None:
        _skip_unless("compose")
        _drive_full_operation_set("compose", tmp_path)


# --- Update path (#88): ref A -> ref B with named-volume preservation ---

_UPDATE_STEPS_IMAGE = [
    "install_ref_a",
    "marker_written",
    "stopped_for_update",
    "updated_to_ref_b",
    "single_container_on_ref_b",
    "volume_preserved",
    "old_image_still_present",
    "stopped_for_backward",
    "backward_to_ref_a",
    "volume_preserved_backward",
]
_UPDATE_STEPS_BUILD = [
    "install_state_one",
    "marker_written",
    "stopped_for_update",
    "rebuilt_state_two",
    "state_two_served",
    "volume_preserved",
]


def _volume_write(client: Any, volume: str, content: str) -> None:
    client.containers.run(
        "busybox:1.36.1",
        ["sh", "-c", f"printf %s '{content}' > /data/marker"],
        volumes={volume: {"bind": "/data", "mode": "rw"}},
        remove=True,
    )


def _volume_read(client: Any, volume: str) -> str:
    out = client.containers.run(
        "busybox:1.36.1",
        ["cat", "/data/marker"],
        volumes={volume: {"bind": "/data", "mode": "ro"}},
        remove=True,
    )
    return str(out.decode("utf-8"))


def _http_body(port: int) -> str:
    import time
    import urllib.request

    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/", timeout=2) as r:
                return str(r.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - retry until the container answers
            time.sleep(0.5)
    return ""


def _real_client() -> Any:
    from docker_app_launcher.docker import py_client

    return py_client.get_client()


def _cleanup_volume(name: str) -> None:
    import contextlib

    client = _real_client()
    try:
        with contextlib.suppress(Exception):
            client.volumes.get(name).remove(force=True)
    finally:
        client.close()


class TestUpdatePathImageMode:
    """#88: the path every user walks on every app update - installed with
    reference A, then updated (= explicit Start, the documented update
    action) to reference B. THE decisive proof: the named volume survives."""

    REF_A = "traefik/whoami:v1.9"
    REF_B = "traefik/whoami:v1.10"

    def test_update_and_backward_preserve_the_volume(self, tmp_path: Path) -> None:
        _skip_unless("image")
        import os

        from docker_app_launcher.docker import lifecycle

        marker = os.urandom(8).hex()
        config = _mode_config("image", tmp_path)
        volume = f"{config.container_name}-data"
        config.image_reference = self.REF_A
        steps: list[str] = []
        lifecycle.uninstall(config)
        _cleanup_volume(volume)
        try:
            ok, msg = lifecycle.install(config)
            assert ok, f"install(ref A) failed: {msg}"
            client = _real_client()
            try:
                assert self.REF_A in (client.containers.get(config.container_name).image.tags or [])
            finally:
                client.close()
            steps.append("install_ref_a")

            client = _real_client()
            try:
                _volume_write(client, volume, marker)
            finally:
                client.close()
            steps.append("marker_written")

            # MEASURED behavior: start() on a RUNNING stack returns
            # already_running and touches nothing - there is no one-action
            # update from the running state. The real update path is
            # stop -> (new reference) -> start; a single-action update is
            # tracked as its own issue.
            ok, msg = lifecycle.stop(config)
            assert ok, f"stop before update failed: {msg}"
            steps.append("stopped_for_update")

            config.image_reference = self.REF_B
            ok, msg = lifecycle.start(config)
            assert ok, f"update to ref B failed: {msg}"
            steps.append("updated_to_ref_b")

            client = _real_client()
            try:
                # REPLACED, not duplicated: exactly one container of the name,
                # and it runs reference B - the two-references proof.
                matching = [c for c in client.containers.list(all=True) if c.name == config.container_name]
                assert len(matching) == 1, f"old container must be replaced, found {len(matching)}"
                assert self.REF_B in (matching[0].image.tags or []), "container must run reference B"
                steps.append("single_container_on_ref_b")

                assert _volume_read(client, volume) == marker, "user data lost on update!"
                steps.append("volume_preserved")

                # Documented behavior (README): the OLD image remains until
                # docker image prune / cleanup - assert so a change is noticed.
                client.images.get(self.REF_A)
                steps.append("old_image_still_present")
            finally:
                client.close()

            # Backward: an OLDER reference. No claim about app functionality,
            # but no data loss and a comprehensible message.
            ok, msg = lifecycle.stop(config)
            assert ok, f"stop before backward update failed: {msg}"
            steps.append("stopped_for_backward")
            config.image_reference = self.REF_A
            ok, msg = lifecycle.start(config)
            assert ok and msg, f"backward update must report comprehensibly: {msg!r}"
            steps.append("backward_to_ref_a")
            client = _real_client()
            try:
                assert _volume_read(client, volume) == marker, "user data lost on backward update!"
                assert self.REF_A in (client.containers.get(config.container_name).image.tags or [])
            finally:
                client.close()
            steps.append("volume_preserved_backward")

            assert steps == _UPDATE_STEPS_IMAGE, f"update path incomplete: {steps}"
        finally:
            lifecycle.uninstall(config)
            _cleanup_volume(volume)


class _UpdateViaRebuild:
    """dockerfile/compose analog (#88): two different source STATES - the
    fixture content changes, Start rebuilds, the volume survives."""

    mode = ""

    def _run(self, tmp_path: Path) -> None:
        import os

        from docker_app_launcher.docker import lifecycle

        marker = os.urandom(8).hex()
        config = _mode_config(self.mode, tmp_path)
        volume = f"{config.container_name}-data"
        install_dir = Path(config.install_dir)
        steps: list[str] = []
        lifecycle.uninstall(config)
        _cleanup_volume(volume)
        try:
            (install_dir / "index.html").write_text("state-one\n", encoding="utf-8")
            ok, msg = lifecycle.install(config)
            assert ok, f"[{self.mode}] install(state one) failed: {msg}"
            assert "state-one" in _http_body(config.default_port)
            steps.append("install_state_one")

            client = _real_client()
            try:
                _volume_write(client, volume, marker)
            finally:
                client.close()
            steps.append("marker_written")

            # Same measured behavior as image mode: update = stop -> start.
            ok, msg = lifecycle.stop(config)
            assert ok, f"[{self.mode}] stop before update failed: {msg}"
            steps.append("stopped_for_update")

            (install_dir / "index.html").write_text("state-two\n", encoding="utf-8")
            ok, msg = lifecycle.start(config)
            assert ok, f"[{self.mode}] rebuild start failed: {msg}"
            steps.append("rebuilt_state_two")

            body = _http_body(config.default_port)
            assert "state-two" in body, f"[{self.mode}] two-states proof failed, body: {body!r}"
            steps.append("state_two_served")

            client = _real_client()
            try:
                assert _volume_read(client, volume) == marker, f"[{self.mode}] user data lost on update!"
            finally:
                client.close()
            steps.append("volume_preserved")

            assert steps == _UPDATE_STEPS_BUILD, f"[{self.mode}] update path incomplete: {steps}"
        finally:
            lifecycle.uninstall(config)
            _cleanup_volume(volume)


class TestUpdatePathDockerfileMode(_UpdateViaRebuild):
    mode = "dockerfile"

    def test_rebuild_preserves_the_volume(self, tmp_path: Path) -> None:
        _skip_unless("dockerfile")
        self._run(tmp_path)


class TestUpdatePathComposeMode(_UpdateViaRebuild):
    mode = "compose"

    def test_rebuild_preserves_the_volume(self, tmp_path: Path) -> None:
        _skip_unless("compose")
        self._run(tmp_path)
