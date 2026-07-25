"""Tests for :mod:`docker_app_launcher.docker.py_client` (#44).

No daemon: docker-py is stubbed at the module attribute, so every verdict
path is deterministic.
"""

from __future__ import annotations

import pytest

from docker_app_launcher.docker import command_runner, py_client


class _FakeClient:
    def __init__(self, ping_exc: BaseException | None = None) -> None:
        self._ping_exc = ping_exc
        self.closed = False

    def ping(self) -> bool:
        if self._ping_exc is not None:
            raise self._ping_exc
        return True

    def close(self) -> None:
        self.closed = True


class _FakeDockerModule:
    """Stands in for the ``docker`` package."""

    def __init__(self, client: _FakeClient | None = None, init_exc: BaseException | None = None) -> None:
        self.client = client or _FakeClient()
        self._init_exc = init_exc
        self.base_urls: list[str] = []

    def from_env(self, timeout: float = 10.0) -> _FakeClient:
        if self._init_exc is not None:
            raise self._init_exc
        return self.client

    def DockerClient(self, base_url: str, timeout: float = 10.0) -> _FakeClient:
        self.base_urls.append(base_url)
        if self._init_exc is not None:
            raise self._init_exc
        return self.client


@pytest.fixture(autouse=True)
def _no_host_override(monkeypatch):
    monkeypatch.setattr(command_runner, "_DOCKER_HOST_OVERRIDE", None)


class TestAvailability:
    def test_unavailable_without_dockerpy(self, monkeypatch) -> None:
        monkeypatch.setattr(py_client, "_dockerpy", None)
        assert py_client.available() is False
        assert py_client.ping() == ("unavailable", "docker-py not importable")

    def test_get_client_raises_without_dockerpy(self, monkeypatch) -> None:
        monkeypatch.setattr(py_client, "_dockerpy", None)
        with pytest.raises(RuntimeError):
            py_client.get_client()


class TestPing:
    def test_ok(self, monkeypatch) -> None:
        fake = _FakeDockerModule()
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        status, _ = py_client.ping()
        assert status == "ok"
        assert fake.client.closed  # client always closed

    def test_daemon_down_connection_refused(self, monkeypatch) -> None:
        fake = _FakeDockerModule(init_exc=ConnectionRefusedError("connect"))
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        status, _ = py_client.ping()
        assert status == "down"

    def test_socket_missing_is_down(self, monkeypatch) -> None:
        fake = _FakeDockerModule(init_exc=FileNotFoundError(2, "No such file"))
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        assert py_client.ping()[0] == "down"

    def test_permission_error_direct(self, monkeypatch) -> None:
        fake = _FakeDockerModule(init_exc=PermissionError(13, "Permission denied"))
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        assert py_client.ping()[0] == "permission"

    def test_permission_error_nested_in_args(self, monkeypatch) -> None:
        # requests/urllib3 shape: ProtocolError('Connection aborted.', PermissionError(13, ...))
        inner = PermissionError(13, "Permission denied")
        wrapper = Exception("Error while fetching server API version", Exception("Connection aborted.", inner))
        fake = _FakeDockerModule(init_exc=wrapper)
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        assert py_client.ping()[0] == "permission"

    def test_permission_error_in_cause_chain(self, monkeypatch) -> None:
        inner = PermissionError(13, "Permission denied")
        wrapper = RuntimeError("wrapped")
        wrapper.__cause__ = inner
        fake = _FakeDockerModule(init_exc=wrapper)
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        assert py_client.ping()[0] == "permission"

    def test_ping_failure_after_connect(self, monkeypatch) -> None:
        fake = _FakeDockerModule(client=_FakeClient(ping_exc=ConnectionResetError("reset")))
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        status, _ = py_client.ping()
        assert status == "down"
        assert fake.client.closed

    def test_explicit_endpoint_used(self, monkeypatch) -> None:
        fake = _FakeDockerModule()
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        py_client.ping("unix:///run/user/1000/docker.sock")
        assert fake.base_urls == ["unix:///run/user/1000/docker.sock"]

    def test_host_override_honored(self, monkeypatch) -> None:
        fake = _FakeDockerModule()
        monkeypatch.setattr(py_client, "_dockerpy", fake)
        monkeypatch.setattr(command_runner, "_DOCKER_HOST_OVERRIDE", "unix:///desktop.sock")
        py_client.ping()
        assert fake.base_urls == ["unix:///desktop.sock"]


class TestClassify:
    def test_cycle_in_context_chain_terminates(self) -> None:
        a = RuntimeError("a")
        b = RuntimeError("b")
        a.__context__ = b
        b.__context__ = a
        assert py_client._classify_exception(a) == "down"

    def test_eperm_errno_counts_as_permission(self) -> None:
        import errno as _errno

        exc = OSError(_errno.EPERM, "Operation not permitted")
        assert py_client._classify_exception(exc) == "permission"
