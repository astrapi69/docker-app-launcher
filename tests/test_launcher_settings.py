"""Tests for :mod:`docker_app_launcher.launcher_settings`.

Split from the old monolithic test_actions.py along the same responsibility
lines as the source (#42). Mocks patch the OWNING module - the facade only
re-exports.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from docker_app_launcher import launcher_settings as settings
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import lifecycle
from tests.conftest import make_result


def _bind_free_port() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _make_repo(config: LauncherConfig) -> None:
    config.compose_path.parent.mkdir(parents=True, exist_ok=True)
    config.compose_path.write_text("services: {}\n")


@pytest.fixture
def iconfig(config):
    """A config that declares two internal ports (backend + nginx)."""
    config.internal_ports = {"backend": 8000, "nginx": 80}
    config.env_internal_port_keys = {"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"}
    config.show_advanced_ports = True
    return config


@contextmanager
def _fake_response(status: int, body: str):
    class _Resp:
        def __init__(self) -> None:
            self.status = status

        def read(self) -> bytes:
            return body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    yield _Resp()


class _FakePopen:
    """Deterministic Popen stand-in: yields prepared lines, then exits."""

    def __init__(self, lines: list[str], returncode: int = 0, hang_after: bool = False) -> None:
        self._lines = lines
        self.returncode = returncode
        self._hang_after = hang_after
        self._killed = threading.Event()
        self.stdout = self._iter_stdout()

    def _iter_stdout(self) -> Iterator[str]:
        yield from (line + "\n" for line in self._lines)
        if self._hang_after:
            # Simulate a process that stops producing output but never exits
            # until the watchdog kills it.
            self._killed.wait(timeout=5.0)

    def kill(self) -> None:
        self.returncode = -9
        self._killed.set()

    def wait(self) -> int:
        return self.returncode


class TestPorts:
    def test_check_port_free(self) -> None:
        ok, msg = settings.check_port(59999)
        assert ok is True and "free" in msg

    def test_check_port_occupied(self) -> None:
        sock, port = _bind_free_port()
        try:
            ok, msg = settings.check_port(port)
            assert ok is False and "occupied" in msg
        finally:
            sock.close()

    def test_check_port_too_low(self) -> None:
        ok, _ = settings.check_port(80)
        assert ok is False

    def test_check_port_too_high(self) -> None:
        ok, _ = settings.check_port(70000)
        assert ok is False

    def test_check_port_rejects_bool(self) -> None:
        ok, _ = settings.check_port(True)
        assert ok is False

    def test_find_free_port_finds_self(self) -> None:
        found, port, _ = settings.find_free_port(59000)
        assert found is True and port >= 59000

    def test_find_free_port_invalid_start(self) -> None:
        found, port, _ = settings.find_free_port(10)
        assert found is False and port == 0

    def test_find_free_port_skips_occupied(self) -> None:
        sock, port = _bind_free_port()
        try:
            found, got, _ = settings.find_free_port(port, max_tries=5)
            assert found is True and got != port
        finally:
            sock.close()


class TestPortPersistence:
    def test_set_and_resolve(self, config) -> None:
        ok, msg = settings.set_port(config, 9000)
        assert ok is True and "9000" in msg
        assert settings.resolve_port(config) == 9000

    def test_set_invalid_rejected(self, config) -> None:
        ok, _ = settings.set_port(config, 1)
        assert ok is False

    def test_resolve_default_when_unset(self, config) -> None:
        assert settings.resolve_port(config) == 8080

    def test_resolve_cli_port_wins(self, config) -> None:
        settings.set_port(config, 9000)
        assert settings.resolve_port(config, cli_port=9100) == 9100

    def test_resolve_ignores_invalid_cli(self, config) -> None:
        settings.set_port(config, 9000)
        assert settings.resolve_port(config, cli_port=1) == 9000

    def test_set_port_writes_env(self, config) -> None:
        settings.set_port(config, 9000)
        env = settings._env_path(config)
        assert env is not None and env.is_file()
        assert "APP_PORT=9000" in env.read_text()

    def test_set_port_upserts_env(self, config) -> None:
        settings.set_port(config, 9000)
        settings.set_port(config, 9100)
        env = settings._env_path(config)
        assert env is not None
        text = env.read_text()
        assert "APP_PORT=9100" in text and "APP_PORT=9000" not in text

    def test_write_env_port_without_install_dir(self, tmp_path) -> None:
        # Regression (Bug 1): with no install_dir the .env must STILL be written,
        # next to the compose file, so `docker compose` actually sees the new
        # port. Previously _env_path returned None and the write was a silent
        # no-op, so the launcher and Compose disagreed on the port.
        compose = tmp_path / "docker-compose.prod.yml"
        compose.write_text("services: {}\n")
        cfg = LauncherConfig(app_name="X", compose_file=str(compose), config_dir=str(tmp_path / ".x")).resolve()
        assert cfg.install_dir == ""
        settings._write_env_port(cfg, 9000)
        env = tmp_path / ".env"
        assert env.is_file() and "APP_PORT=9000" in env.read_text()

    def test_load_config_missing(self, tmp_path) -> None:
        assert settings.load_config(tmp_path / "no.json") == {}

    def test_set_and_resolve_locale(self, config) -> None:
        settings.set_locale(config, "fr")
        assert settings.resolve_locale(config) == "fr"

    def test_resolve_locale_defaults_to_config(self, config) -> None:
        # the config fixture pins locale="en"
        assert settings.resolve_locale(config) == "en"

    def test_resolve_locale_unknown_falls_back_en(self, config) -> None:
        settings.save_config(config.launcher_config_file, {"locale": "zz"})
        assert settings.resolve_locale(config) == "en"

    def test_save_load_round_trip(self, tmp_path) -> None:
        path = tmp_path / "c.json"
        settings.save_config(path, {"port": 1234})
        assert settings.load_config(path) == {"port": 1234}


class TestInternalPorts:
    def test_validate_allows_low_ports(self) -> None:
        # Internal ports are not host-published, so 80 is valid (unlike a host port).
        assert settings._validate_internal_port(80)[0] is True
        assert settings._validate_internal_port(0)[0] is False
        assert settings._validate_internal_port(70000)[0] is False

    def test_resolve_default_from_config(self, iconfig) -> None:
        assert settings.resolve_internal_port(iconfig, "backend") == 8000
        assert settings.resolve_internal_port(iconfig, "nginx") == 80

    def test_resolve_override_wins(self, iconfig) -> None:
        settings.set_internal_port(iconfig, "backend", 9001)
        assert settings.resolve_internal_port(iconfig, "backend") == 9001

    def test_resolve_invalid_override_ignored(self, iconfig) -> None:
        settings.save_config(iconfig.launcher_config_file, {"internal_ports": {"backend": 70000}})
        assert settings.resolve_internal_port(iconfig, "backend") == 8000

    def test_set_unknown_name_rejected(self, iconfig) -> None:
        ok, msg = settings.set_internal_port(iconfig, "db", 5432)
        assert ok is False and "db" in msg

    def test_set_persists_and_writes_env(self, iconfig) -> None:
        ok, _ = settings.set_internal_port(iconfig, "backend", 9001)
        assert ok is True
        env = settings._env_path(iconfig).read_text()
        assert "APP_BACKEND_PORT=9001" in env

    def test_write_env_ports_writes_all_ports(self, iconfig) -> None:
        # The .env write self-creates its parent dir, so no repo scaffolding needed.
        settings._write_env_ports(iconfig)
        env = settings._env_path(iconfig).read_text()
        assert f"{iconfig.env_port_key}=" in env
        assert "APP_BACKEND_PORT=8000" in env
        assert "APP_NGINX_PORT=80" in env

    def test_change_unknown_name_rejected(self, iconfig, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        ok, _ = lifecycle.change_internal_port(iconfig, "db", 5432)
        assert ok is False

    def test_change_invalid_port_rejected(self, iconfig) -> None:
        ok, _ = lifecycle.change_internal_port(iconfig, "backend", 0)
        assert ok is False

    def test_change_not_running_only_persists(self, iconfig, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "stopped")
        ok, _ = lifecycle.change_internal_port(iconfig, "backend", 9001)
        assert ok is True
        assert settings.resolve_internal_port(iconfig, "backend") == 9001

    def test_change_running_rebuilds(self, iconfig, monkeypatch) -> None:
        # An internal-port change MUST rebuild (up --build -d), not just restart.
        _make_repo(iconfig)
        captured: dict[str, tuple[str, ...]] = {}
        states = iter(["running", "running"])
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
        monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))

        def fake_stream(c, *args, **kwargs):
            captured["args"] = args
            return (0, "")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: make_result(stdout=""))
        ok, msg = lifecycle.change_internal_port(iconfig, "backend", 9001)
        assert ok is True and "9001" in msg
        assert captured["args"] == ("up", "--build", "-d")

    def test_change_stop_failure_aborts(self, iconfig, monkeypatch) -> None:
        _make_repo(iconfig)  # the rebuild path now runs the build capability gate first (#54)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        monkeypatch.setattr(lifecycle, "stop", lambda c: (False, "cannot stop"))
        ok, msg = lifecycle.change_internal_port(iconfig, "backend", 9001)
        assert ok is False and "cannot stop" in msg
