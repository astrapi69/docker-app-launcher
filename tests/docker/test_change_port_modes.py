"""The host-port change per deployment mode (#112) - docker-py mocked, no daemon.

Measured defect this suite pins: ``change_port`` recreated the stack through
``docker compose up -d`` in EVERY mode, so image and dockerfile mode failed with
``open .../docker-compose.prod.yml: no such file or directory`` - a compose file
the app never had. The port was persisted, the running container kept the old
one.

The second pin is the release-manager's condition on the fix (#111): whatever
recreates the container MUST publish through ``port_binding(config, host_port)``.
A recreate that publishes bare would silently republish on 0.0.0.0 while the
config still says 127.0.0.1 - the security fix undone by a detour.
"""

from __future__ import annotations

from typing import Any

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import dockerfile_backend, image_backend, lifecycle, py_client
from docker_app_launcher.launcher_settings import resolve_port


class _FakeContainers:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] = {}
        self.removed: list[str] = []

    def get(self, name: str) -> Any:
        raise RuntimeError("no such container")

    def run(self, image: str, **kwargs: Any) -> Any:
        self.run_kwargs = {"image": image, **kwargs}
        return object()


class _FakeImages:
    def get(self, ref: str) -> Any:
        return object()


class _FakeClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()
        self.images = _FakeImages()
        self.api = object()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _config(tmp_path, mode: str, **kwargs: Any) -> LauncherConfig:
    install_dir = tmp_path / mode
    install_dir.mkdir(parents=True, exist_ok=True)
    if mode == "dockerfile":
        (install_dir / "Dockerfile").write_text("FROM busybox:1.36.1\n", encoding="utf-8")
    return LauncherConfig(
        app_name=f"Port {mode}",
        container_name=f"port-{mode}",
        compose_project=f"port-{mode}",
        image_name=f"port-{mode}",
        deployment_mode=mode,
        image_reference="ghcr.io/owner/app:2.0.0" if mode == "image" else "",
        container_port=80,
        install_dir=str(install_dir),
        config_dir=str(tmp_path / f".{mode}"),
        default_port=8080,
        locale="en",
        **kwargs,
    ).resolve()


@pytest.fixture
def running_stack(monkeypatch):
    """A running stack whose stop/health steps always succeed, so each test
    measures only WHAT recreates the container."""
    states = iter(["running", "running"])
    monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
    monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
    monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))
    monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))


@pytest.fixture
def no_compose(monkeypatch):
    """Compose is not merely unused in the SDK modes - it must not be REACHED.

    Both the guard and the stream fail loudly, so a surviving compose detour
    shows up as this error instead of a silently green test.
    """

    def forbidden(*args: Any, **kwargs: Any):
        raise AssertionError("the compose path must not be reached in image/dockerfile mode")

    monkeypatch.setattr(lifecycle, "_stream_compose", forbidden)
    monkeypatch.setattr(lifecycle, "_ensure_compose", forbidden)


@pytest.fixture
def fake_client(monkeypatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(py_client, "get_client", lambda *a, **k: client)
    monkeypatch.setattr(py_client, "available", lambda: True)
    return client


class TestImageModePortChange:
    def test_recreates_through_the_engine_api(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        config = _config(tmp_path, "image")
        ok, msg = lifecycle.change_port(config, 9000)
        assert ok, f"image-mode port change failed: {msg}"
        assert "9000" in msg
        assert resolve_port(config) == 9000
        assert fake_client.containers.run_kwargs["image"] == config.image_reference

    def test_publishes_the_new_port_on_localhost(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        # The release-manager's condition (#111 x #112): the recreate publishes
        # through port_binding, so a port change cannot reopen the interface.
        config = _config(tmp_path, "image")
        assert lifecycle.change_port(config, 9000)[0]
        assert fake_client.containers.run_kwargs["ports"] == {"80/tcp": ("127.0.0.1", 9000)}

    def test_carries_an_explicit_bind_address(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        config = _config(tmp_path, "image", bind_address="192.168.1.10")
        assert lifecycle.change_port(config, 9000)[0]
        assert fake_client.containers.run_kwargs["ports"] == {"80/tcp": ("192.168.1.10", 9000)}

    def test_does_not_re_pull_the_image(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        # A host-port change touches the published port only - re-acquiring a
        # multi-hundred-MB image for it would be the minutes the compose path
        # deliberately avoids with 'up -d' instead of 'up --build -d'.
        def forbidden(*args: Any, **kwargs: Any):
            raise AssertionError("a port change must not re-acquire the image")

        monkeypatch.setattr(image_backend, "_acquire_image", forbidden)
        config = _config(tmp_path, "image")
        assert lifecycle.change_port(config, 9000)[0]

    def test_recreate_failure_is_reported(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        monkeypatch.setattr(image_backend, "recreate", lambda c: (1, "engine said no"))
        config = _config(tmp_path, "image")
        ok, msg = lifecycle.change_port(config, 9000)
        assert not ok
        assert "engine said no" in msg
        assert "docker-compose" not in msg


class TestDockerfileModePortChange:
    def test_recreates_through_the_engine_api(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        config = _config(tmp_path, "dockerfile")
        ok, msg = lifecycle.change_port(config, 9100)
        assert ok, f"dockerfile-mode port change failed: {msg}"
        assert resolve_port(config) == 9100
        assert fake_client.containers.run_kwargs["image"] == config.image_name

    def test_publishes_the_new_port_on_localhost(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        config = _config(tmp_path, "dockerfile")
        assert lifecycle.change_port(config, 9100)[0]
        assert fake_client.containers.run_kwargs["ports"] == {"80/tcp": ("127.0.0.1", 9100)}

    def test_does_not_rebuild_the_image(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        def forbidden(*args: Any, **kwargs: Any):
            raise AssertionError("a host-port change must not rebuild")

        monkeypatch.setattr(dockerfile_backend, "_build", forbidden)
        config = _config(tmp_path, "dockerfile")
        assert lifecycle.change_port(config, 9100)[0]

    def test_recreate_failure_is_reported(self, tmp_path, monkeypatch, running_stack, no_compose, fake_client):
        monkeypatch.setattr(dockerfile_backend, "recreate", lambda c: (1, "engine said no"))
        config = _config(tmp_path, "dockerfile")
        ok, msg = lifecycle.change_port(config, 9100)
        assert not ok and "engine said no" in msg


class TestComposeModePortChangeUnchanged:
    def test_still_recreates_via_compose_without_build(self, tmp_path, monkeypatch, running_stack, fake_client):
        captured: dict[str, tuple[str, ...]] = {}

        def fake_stream(c, *args, **kwargs):
            captured["args"] = args
            return (0, "")

        (tmp_path / "compose").mkdir(parents=True, exist_ok=True)
        config = _config(tmp_path, "compose")
        config.compose_path.write_text("services: {}\n", encoding="utf-8")
        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        monkeypatch.setattr(lifecycle, "_ensure_compose", lambda c: None)
        ok, _ = lifecycle.change_port(config, 9200)
        assert ok
        assert captured["args"] == ("up", "-d")

    def test_the_compose_guard_still_blocks(self, tmp_path, monkeypatch, running_stack):
        config = _config(tmp_path, "compose")
        monkeypatch.setattr(lifecycle, "_ensure_compose", lambda c: (False, "compose missing"))
        ok, msg = lifecycle.change_port(config, 9200)
        assert not ok and "compose missing" in msg


class TestSdkModeGate:
    def test_missing_dockerpy_blocks_before_stopping(self, tmp_path, monkeypatch, running_stack, no_compose):
        # Fail BEFORE the stack is stopped: a gate that only fires after the
        # stop leaves the user with a stopped app and a persisted port.
        monkeypatch.setattr(py_client, "available", lambda: False)

        def forbidden(config):
            raise AssertionError("the gate must refuse before stop()")

        monkeypatch.setattr(lifecycle, "stop", forbidden)
        config = _config(tmp_path, "image")
        ok, msg = lifecycle.change_port(config, 9000)
        assert not ok
        assert "docker" in msg.lower()
        assert resolve_port(config) == 8080, "a blocked port change must not persist the new port"


class TestInternalPortModeRefusal:
    """The internal port is a compose-file concept (env keys the yaml reads).

    Outside compose mode it must say so, not fall into the same compose detour
    #112 is about - and not answer with the misleading 'unknown internal port'
    that an empty key map would produce.
    """

    @pytest.mark.parametrize("mode", ["image", "dockerfile"])
    def test_refuses_outside_compose_mode(self, tmp_path, monkeypatch, mode, no_compose):
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        config = _config(tmp_path, mode)
        config.env_internal_port_keys = {"backend": "APP_BACKEND_PORT"}
        ok, msg = lifecycle.change_internal_port(config, "backend", 9001)
        assert not ok
        assert mode in msg
        assert "unknown" not in msg.lower()
