"""Tests for the dockerfile deployment mode (#51) - docker-py mocked, no daemon."""

from __future__ import annotations

from typing import Any

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import dockerfile_backend, lifecycle, py_client


class _FakeApi:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.build_kwargs: dict[str, Any] = {}

    def build(self, **kwargs: Any):
        self.build_kwargs = kwargs
        yield from self._chunks


class _FakeContainers:
    def __init__(self, existing: Any | None = None) -> None:
        self._existing = existing
        self.run_kwargs: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        if self._existing is None:
            raise RuntimeError(f"no such container: {name}")
        return self._existing

    def run(self, image: str, **kwargs: Any) -> Any:
        self.run_kwargs = {"image": image, **kwargs}
        return object()


class _FakeContainer:
    def __init__(self, logs: bytes = b"") -> None:
        self._logs = logs
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = force

    def logs(self, tail: int = 0) -> bytes:
        return self._logs


class _FakeClient:
    def __init__(self, chunks: list[dict[str, Any]], existing: Any | None = None) -> None:
        self.api = _FakeApi(chunks)
        self.containers = _FakeContainers(existing)
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def dcfg(tmp_path):
    cfg = LauncherConfig(
        app_name="Solo App",
        deployment_mode="dockerfile",
        install_dir=str(tmp_path / "repo"),
        default_port=8080,
        container_volumes={"solo-data": "/app/data"},
        container_env={"SOLO_DEBUG": "false"},
        locale="en",
    ).resolve()
    cfg.build_context_path.mkdir(parents=True, exist_ok=True)
    cfg.dockerfile_path.write_text("FROM scratch\n", encoding="utf-8")
    return cfg


def _wire(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(py_client, "get_client", lambda *a, **k: client)
    monkeypatch.setattr(py_client, "available", lambda: True)


class TestDockerfileUp:
    def test_build_streams_and_runs_container(self, dcfg, monkeypatch) -> None:
        client = _FakeClient([{"stream": "Step 1/2 : FROM scratch\n"}, {"stream": "Successfully built abc\n"}])
        _wire(monkeypatch, client)
        seen: list[str] = []
        rc, detail = dockerfile_backend.up(dcfg, on_output=seen.append)
        assert rc == 0 and detail == ""
        assert seen == ["Step 1/2 : FROM scratch", "Successfully built abc"]
        assert client.api.build_kwargs["tag"] == dcfg.image_name
        run = client.containers.run_kwargs
        assert run["image"] == dcfg.image_name
        assert run["name"] == dcfg.container_name
        assert run["ports"] == {"8080/tcp": 8080}  # container_port=0 -> same as host
        assert run["volumes"] == {"solo-data": {"bind": "/app/data", "mode": "rw"}}
        assert run["environment"] == {"SOLO_DEBUG": "false"}
        assert run["restart_policy"] == {"Name": "unless-stopped"}
        assert client.closed

    def test_distinct_container_port(self, dcfg, monkeypatch) -> None:
        dcfg.container_port = 80
        client = _FakeClient([{"stream": "ok\n"}])
        _wire(monkeypatch, client)
        dockerfile_backend.up(dcfg)
        assert client.containers.run_kwargs["ports"] == {"80/tcp": 8080}

    def test_build_error_is_the_detail(self, dcfg, monkeypatch) -> None:
        client = _FakeClient([{"stream": "Step 1/2\n"}, {"errorDetail": {"message": "COPY failed: nope"}}])
        _wire(monkeypatch, client)
        rc, detail = dockerfile_backend.up(dcfg)
        assert rc == 1 and "COPY failed" in detail
        assert client.containers.run_kwargs == {}, "no container run after a failed build"

    def test_existing_container_removed_before_recreate(self, dcfg, monkeypatch) -> None:
        stale = _FakeContainer()
        client = _FakeClient([{"stream": "ok\n"}], existing=stale)
        _wire(monkeypatch, client)
        rc, _ = dockerfile_backend.up(dcfg)
        assert rc == 0 and stale.removed is True

    def test_missing_dockerfile_fails_before_build(self, dcfg, monkeypatch) -> None:
        dcfg.dockerfile_path.unlink()
        client = _FakeClient([])
        _wire(monkeypatch, client)
        rc, detail = dockerfile_backend.up(dcfg)
        assert rc == 1 and "Dockerfile not found" in detail

    def test_permission_error_is_classified(self, dcfg, monkeypatch) -> None:
        def boom(*a, **k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(py_client, "get_client", boom)
        rc, detail = dockerfile_backend.up(dcfg)
        assert rc == 1 and "permission" in detail.lower()
        assert "docker' group" in detail or "docker" in detail


class TestDockerfileTailLogs:
    def test_returns_decoded_tail(self, dcfg, monkeypatch) -> None:
        container = _FakeContainer(logs=b"boot\nready\n")
        client = _FakeClient([], existing=container)
        _wire(monkeypatch, client)
        ok, text = dockerfile_backend.tail_logs(dcfg, lines=50)
        assert ok is True and text == "boot\nready"

    def test_missing_container_is_a_failed_result(self, dcfg, monkeypatch) -> None:
        client = _FakeClient([], existing=None)
        _wire(monkeypatch, client)
        ok, _ = dockerfile_backend.tail_logs(dcfg, lines=50)
        assert ok is False


class TestLifecycleDispatch:
    """install/start in dockerfile mode never touch compose (#51)."""

    def _base(self, dcfg, monkeypatch, states: list[str]) -> list[str]:
        it = iter(states)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(it))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        compose_calls: list[str] = []

        def no_compose(*a, **k):
            compose_calls.append("called")
            raise AssertionError("compose must not run in dockerfile mode")

        monkeypatch.setattr(lifecycle, "_stream_compose", no_compose)

        def guard_recorder(c):
            compose_calls.append("guard")
            return None

        monkeypatch.setattr(lifecycle, "_ensure_compose", guard_recorder)
        return compose_calls

    def test_install_uses_the_dockerfile_backend(self, dcfg, monkeypatch) -> None:
        compose_calls = self._base(dcfg, monkeypatch, ["not_installed", "running"])
        monkeypatch.setattr(dockerfile_backend, "up", lambda c, **k: (0, ""))
        ok, msg = lifecycle.install(dcfg)
        assert ok is True and "ready" in msg
        assert compose_calls == [], "no compose guard, no compose invocation"

    def test_install_missing_dockerfile_is_actionable(self, dcfg, monkeypatch) -> None:
        self._base(dcfg, monkeypatch, ["not_installed"])
        dcfg.dockerfile_path.unlink()
        ok, msg = lifecycle.install(dcfg)
        assert ok is False and "Dockerfile" in msg and str(dcfg.dockerfile_path) in msg

    def test_install_without_dockerpy_is_actionable(self, dcfg, monkeypatch) -> None:
        self._base(dcfg, monkeypatch, ["not_installed"])
        monkeypatch.setattr(py_client, "available", lambda: False)
        ok, msg = lifecycle.install(dcfg)
        assert ok is False and "docker-py" in msg

    def test_start_uses_the_dockerfile_backend(self, dcfg, monkeypatch) -> None:
        self._base(dcfg, monkeypatch, ["stopped", "running"])
        monkeypatch.setattr(dockerfile_backend, "up", lambda c, **k: (0, ""))
        ok, _msg = lifecycle.start(dcfg)
        assert ok is True

    def test_app_logs_uses_the_dockerfile_backend(self, dcfg, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        monkeypatch.setattr(dockerfile_backend, "tail_logs", lambda c, *, lines: (True, "web ready"))
        ok, text = lifecycle.app_logs(dcfg)
        assert ok is True and text == "web ready"


class TestDeploymentModeSchema:
    def test_default_rule_is_compose(self) -> None:
        # Existing configs (a configured compose file) keep working unchanged.
        assert LauncherConfig(app_name="X").resolve().effective_deployment_mode == "compose"

    def test_explicit_dockerfile(self) -> None:
        cfg = LauncherConfig(app_name="X", deployment_mode="dockerfile").resolve()
        assert cfg.effective_deployment_mode == "dockerfile"

    def test_unknown_mode_is_a_hard_error(self) -> None:
        with pytest.raises(ValueError, match="deployment_mode"):
            LauncherConfig(app_name="X", deployment_mode="swarm").resolve()

    def test_dockerfile_paths_derive_from_install_dir(self, tmp_path) -> None:
        cfg = LauncherConfig(
            app_name="X",
            deployment_mode="dockerfile",
            install_dir=str(tmp_path),
            build_context="src",
            dockerfile_file="deploy/Dockerfile.prod",
        ).resolve()
        assert cfg.build_context_path == tmp_path / "src"
        assert cfg.dockerfile_path == tmp_path / "src" / "deploy" / "Dockerfile.prod"
