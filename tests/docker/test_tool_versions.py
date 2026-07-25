"""Tests for :mod:`docker_app_launcher.docker.tool_versions` (#54).

Version normalization is exercised against the DIRTY real-world strings the
prompt named (Debian ``+dfsg1``, buildx ``-docker`` suffix, desktop builds),
because a string compare or a home-grown parser would get exactly those
wrong. Comparison itself is delegated to ``packaging`` - the tests only pin
the normalization + the intrinsic buildx threshold logic.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest
from packaging.version import Version

from docker_app_launcher.docker import tool_versions
from tests.conftest import make_result

_REAL_PROBE = tool_versions._probe_versions


@pytest.fixture(autouse=True)
def _real_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Undo the global conftest pin: these tests exercise the real probe/parse."""
    monkeypatch.setattr(tool_versions, "_probe_versions", _REAL_PROBE)
    tool_versions.reset_versions_cache()
    yield
    tool_versions.reset_versions_cache()


class TestParseVersion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0.17.0", "0.17.0"),
            ("v0.8.2-docker", "0.8.2"),  # buildx suffix
            ("20.10.21+dfsg1", "20.10.21"),  # Debian engine build metadata
            ("v0.25.0-desktop.1", "0.25.0"),  # Docker Desktop buildx build
            ("github.com/docker/buildx v0.17.1 257815a", "0.17.1"),  # full buildx line
            ("Docker Compose version v2.40.2", "2.40.2"),
            ("1.47", "1.47"),  # two-component API version
            ("29.3.1", "29.3.1"),
        ],
    )
    def test_dirty_strings_normalize(self, raw, expected) -> None:
        assert tool_versions.parse_version(raw) == Version(expected)

    @pytest.mark.parametrize("raw", ["latest", "", "unknown", "not a version"])
    def test_unparsable_is_none(self, raw) -> None:
        assert tool_versions.parse_version(raw) is None

    def test_ordering_is_numeric_not_lexical(self) -> None:
        # The whole point: "0.8.2" must be LESS than "0.17.0" (a string compare
        # would put "0.8" after "0.17").
        lo = tool_versions.parse_version("0.8.2")
        hi = tool_versions.parse_version("0.17.0")
        assert lo is not None and hi is not None
        assert lo < hi


class TestIntrinsicBuildxRequirement:
    def test_modern_compose_gates_buildx_017(self) -> None:
        assert tool_versions.intrinsic_buildx_requirement(Version("2.40.2")) == Version("0.17.0")
        assert tool_versions.intrinsic_buildx_requirement(Version("2.41.0")) == Version("0.17.0")

    def test_compose_below_the_gate_does_not_require_buildx(self) -> None:
        # v2.40.1 and older do not emit the buildx-0.17 error, so blocking
        # there would be a false positive.
        assert tool_versions.intrinsic_buildx_requirement(Version("2.40.1")) is None
        assert tool_versions.intrinsic_buildx_requirement(Version("2.37.0")) is None
        assert tool_versions.intrinsic_buildx_requirement(Version("1.29.2")) is None

    def test_unknown_compose_asserts_nothing(self) -> None:
        assert tool_versions.intrinsic_buildx_requirement(None) is None


class TestDetectToolVersions:
    def _mock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        server: str = "27.5.1",
        client: str = "27.5.1",
        api: str = "1.47",
        compose: str = "2.40.2",
        buildx: str = "0.20.0",
    ) -> None:
        def fake_run(cmd: list[str], **k: object) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["docker", "version", "--format"]:
                return make_result(stdout=f"{server}\t{client}\t{api}\n")
            if cmd[:3] == ["docker", "compose", "version"]:
                return make_result(stdout=f"{compose}\n")
            if cmd[:3] == ["docker", "buildx", "version"]:
                return make_result(stdout=f"github.com/docker/buildx v{buildx} deadbeef\n")
            raise AssertionError(f"unexpected cmd: {cmd}")

        monkeypatch.setattr(tool_versions, "_run", fake_run)

    def test_reads_every_link(self, config, monkeypatch) -> None:
        self._mock(monkeypatch, server="20.10.21+dfsg1", compose="2.40.2", buildx="0.8.2")
        tv = tool_versions.detect_tool_versions(config)
        assert tv.engine == Version("20.10.21")
        assert tv.compose == Version("2.40.2")
        assert tv.buildx == Version("0.8.2")
        assert tv.api == Version("1.47")

    def test_result_is_cached(self, config, monkeypatch) -> None:
        calls = {"n": 0}

        def counting(cmd, **k):
            calls["n"] += 1
            return make_result(stdout="27.5.1\t27.5.1\t1.47\n")

        monkeypatch.setattr(tool_versions, "_run", counting)
        tool_versions.detect_tool_versions(config)
        first = calls["n"]
        tool_versions.detect_tool_versions(config)
        assert calls["n"] == first, "second call must serve the cache"

    def test_log_line_names_every_link(self, config, monkeypatch, caplog) -> None:
        self._mock(monkeypatch, server="27.5.1", compose="2.40.2", buildx="0.8.2")
        with caplog.at_level("INFO"):
            tool_versions.detect_tool_versions(config)
        line = "".join(r.message for r in caplog.records if "toolchain" in r.message)
        assert "engine=27.5.1" in line and "compose=2.40.2" in line and "buildx=0.8.2" in line

    def test_missing_binary_degrades_to_empty(self, config, monkeypatch) -> None:
        def boom(cmd, **k):
            raise FileNotFoundError("docker not found")

        monkeypatch.setattr(tool_versions, "_run", boom)
        tv = tool_versions.detect_tool_versions(config)
        assert tv.engine is None and tv.buildx is None and tv.compose is None

    def test_probe_timeout_degrades(self, config, monkeypatch) -> None:
        def slow(cmd, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(tool_versions, "_run", slow)
        tv = tool_versions.detect_tool_versions(config)
        assert tv.buildx is None
