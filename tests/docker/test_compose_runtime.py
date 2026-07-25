"""Tests for :mod:`docker_app_launcher.docker.compose_runtime` (#48).

The three verified environment variants - plugin-only, legacy-v1-only,
none - plus the v1 file-compatibility gate and the process cache. All
probes are mocked at ``_run``; the conftest pin on ``_probe`` is undone
per test so the REAL ladder logic runs.
"""

from __future__ import annotations

import subprocess

import pytest

from docker_app_launcher.docker import compose_runtime
from tests.conftest import make_result

_REAL_PROBE = compose_runtime._probe


@pytest.fixture(autouse=True)
def _real_ladder(monkeypatch):
    """Undo the global conftest pin: these tests exercise the actual probe."""
    monkeypatch.setattr(compose_runtime, "_probe", _REAL_PROBE)
    compose_runtime.reset_compose_cache()
    yield
    compose_runtime.reset_compose_cache()


def _env(monkeypatch, *, plugin: bool, legacy: bool, v1_parses: bool = True) -> list[list[str]]:
    """Simulate one environment variant; returns the probe-call log."""
    calls: list[list[str]] = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "version"]:
            if plugin:
                return make_result(stdout="Docker Compose version v2.24.0")
            # Verified 20.10-without-plugin behaviour: flag error + help dump.
            return make_result(returncode=125, stderr="unknown shorthand flag: 'p' in -p")
        if cmd[0] == "docker-compose":
            if not legacy:
                raise FileNotFoundError("docker-compose not found")
            if cmd[1] == "--version":
                return make_result(stdout="docker-compose version 1.29.2, build unknown")
            if "config" in cmd:
                if v1_parses:
                    return make_result()
                return make_result(returncode=1, stderr="Unsupported config option for services: 'profiles'")
        raise AssertionError(f"unexpected probe: {cmd}")

    monkeypatch.setattr(compose_runtime, "_run", fake_run)
    return calls


class TestDetectLadder:
    def test_plugin_wins(self, config, monkeypatch) -> None:
        _env(monkeypatch, plugin=True, legacy=True)
        frontend, detail = compose_runtime.detect_compose(config)
        assert frontend == "plugin"
        assert "v2.24.0" in detail

    def test_legacy_accepted_when_it_parses_the_file(self, config, monkeypatch) -> None:
        calls = _env(monkeypatch, plugin=False, legacy=True, v1_parses=True)
        frontend, detail = compose_runtime.detect_compose(config)
        assert frontend == "legacy"
        assert "1.29.2" in detail
        assert any("config" in c for c in calls), "v1 must be validated against THIS compose file"

    def test_legacy_rejected_when_file_is_not_v1_parseable(self, config, monkeypatch) -> None:
        _env(monkeypatch, plugin=False, legacy=True, v1_parses=False)
        frontend, detail = compose_runtime.detect_compose(config)
        assert frontend == "legacy_incompatible"
        assert "profiles" in detail

    def test_none_when_nothing_exists(self, config, monkeypatch) -> None:
        _env(monkeypatch, plugin=False, legacy=False)
        assert compose_runtime.detect_compose(config)[0] == "none"

    def test_result_is_cached_per_process(self, config, monkeypatch) -> None:
        calls = _env(monkeypatch, plugin=True, legacy=False)
        compose_runtime.detect_compose(config)
        first = len(calls)
        compose_runtime.detect_compose(config)
        assert len(calls) == first, "second call must serve the cache"

    def test_probe_timeout_is_none_verdict(self, config, monkeypatch) -> None:
        def boom(cmd, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(compose_runtime, "_run", boom)
        assert compose_runtime.detect_compose(config)[0] == "none"


class TestComposeBaseArgs:
    def test_plugin_base(self, config, monkeypatch) -> None:
        _env(monkeypatch, plugin=True, legacy=False)
        assert compose_runtime.compose_base_args(config) == ["docker", "compose"]

    def test_legacy_base(self, config, monkeypatch) -> None:
        _env(monkeypatch, plugin=False, legacy=True)
        assert compose_runtime.compose_base_args(config) == ["docker-compose"]


class TestComposeAvailable:
    @pytest.mark.parametrize(
        ("plugin", "legacy", "v1_parses", "usable"),
        [
            (True, False, True, True),
            (False, True, True, True),
            (False, True, False, False),
            (False, False, True, False),
        ],
    )
    def test_verdicts(self, config, monkeypatch, plugin, legacy, v1_parses, usable) -> None:
        _env(monkeypatch, plugin=plugin, legacy=legacy, v1_parses=v1_parses)
        assert compose_runtime.compose_available(config)[0] is usable
