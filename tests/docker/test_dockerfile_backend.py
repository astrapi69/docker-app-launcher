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
        # mirror docker-py's private attrs the #77 code touches
        self._auth_configs: Any = None
        self._proxy_configs: Any = None

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


class _CredsSentinel:
    """Stands in for docker-py's filesystem-loaded AuthConfig: resolving it
    (get_all_credentials) is exactly what explodes on a broken credsStore."""

    is_empty = False

    def get_all_credentials(self):
        raise AssertionError("credential resolution must never be triggered for a local build (#77)")


class TestRegistryAuthNeutralized:
    """#77: a stale credsStore (docker-credential-gcloud leftover) hard-fails
    docker-py builds the CLI would run. Local builds of public base images
    need no registry login - the resolution must not even START."""

    def test_build_runs_with_auth_neutralized(self, dcfg, monkeypatch) -> None:
        client = _FakeClient([{"stream": "ok\n"}])
        client.api._auth_configs = _CredsSentinel()
        _wire(monkeypatch, client)
        rc, detail = dockerfile_backend.up(dcfg)
        assert rc == 0, f"build must succeed without touching the credential store: {detail}"
        replaced = client.api._auth_configs
        assert not isinstance(replaced, _CredsSentinel), "auth config must be replaced before the build"
        assert replaced.get_all_credentials() == {}
        assert replaced.is_empty is False, "an 'empty' config would make docker-py RELOAD the broken file"

    def test_store_error_is_classified_actionably(self, dcfg, monkeypatch) -> None:
        class StoreError(Exception):
            pass

        def boom(*a, **k):
            raise StoreError("docker-credential-gcloud not installed or not available in PATH")

        monkeypatch.setattr(py_client, "get_client", boom)
        rc, detail = dockerfile_backend.up(dcfg)
        assert rc == 1
        assert "credsStore" in detail or "credential helper" in detail
        assert "config.json" in detail, "must point at the file to fix, not just echo the library error"

    def test_opt_in_keeps_resolution_active(self, dcfg, monkeypatch) -> None:
        # A consumer that declares private registries gets the REAL resolution
        # (and thus real errors) - the launcher only neutralizes by default.
        dcfg.use_registry_credentials = True
        client = _FakeClient([{"stream": "ok\n"}])
        sentinel = _CredsSentinel()
        client.api._auth_configs = sentinel

        def quiet_build(**kwargs):
            yield {"stream": "ok\n"}  # bypass the sentinel's get_all_credentials

        client.api.build = quiet_build  # type: ignore[method-assign]
        _wire(monkeypatch, client)
        dockerfile_backend.up(dcfg)
        assert client.api._auth_configs is sentinel, "opt-in must NOT replace the auth config"

    def test_opt_in_store_error_message_demands_repair(self, dcfg, monkeypatch) -> None:
        dcfg.use_registry_credentials = True

        class StoreError(Exception):
            pass

        monkeypatch.setattr(py_client, "get_client", lambda *a, **k: (_ for _ in ()).throw(StoreError("gcloud gone")))
        rc, detail = dockerfile_backend.up(dcfg)
        assert rc == 1 and "use_registry_credentials" in detail and "repair" in detail


class _FakeProxyConfig:
    def __init__(self, env: dict[str, str]) -> None:
        self._env = env
        self.asked = False

    def get_environment(self) -> dict[str, str]:
        self.asked = True
        return dict(self._env)


class _ProxyApiClient(_FakeClient):
    def __init__(self, chunks: list[dict[str, Any]], proxies: dict[str, str]) -> None:
        super().__init__(chunks)
        self.api._proxy_configs = _FakeProxyConfig(proxies)


class TestProxyLogging:
    """#77 part 2: proxies pass through (masking would break auth proxies),
    but credentials must NEVER appear in any log line."""

    def test_credentialed_proxy_warns_masked_and_never_leaks(self, dcfg, monkeypatch, caplog) -> None:
        import logging as _logging

        client = _ProxyApiClient([{"stream": "ok\n"}], {"HTTPS_PROXY": "http://alice:s3cr3tpw@proxy.corp:3128"})
        _wire(monkeypatch, client)
        with caplog.at_level(_logging.INFO, logger="docker_app_launcher.docker.dockerfile_backend"):
            rc, _ = dockerfile_backend.up(dcfg)
        assert rc == 0
        assert client.api._proxy_configs.asked, "test contract: the proxy config was actually consulted"
        rendered = [r.getMessage() for r in caplog.records]
        assert any("HTTPS_PROXY" in m for m in rendered), "the variable NAME must be announced"
        assert any("alice:***@proxy.corp:3128" in m for m in rendered), "warning must show the masked form"
        assert not any("s3cr3tpw" in m for m in rendered), "the password must never reach the log"

    def test_plain_proxy_gets_info_only(self, dcfg, monkeypatch, caplog) -> None:
        import logging as _logging

        client = _ProxyApiClient([{"stream": "ok\n"}], {"HTTP_PROXY": "http://proxy.corp:3128"})
        _wire(monkeypatch, client)
        with caplog.at_level(_logging.INFO, logger="docker_app_launcher.docker.dockerfile_backend"):
            dockerfile_backend.up(dcfg)
        rendered = [r.getMessage() for r in caplog.records]
        assert any("HTTP_PROXY" in m and "apply to this build" in m for m in rendered)
        assert not any(r.levelname == "WARNING" and "credentials" in r.getMessage() for r in caplog.records)
