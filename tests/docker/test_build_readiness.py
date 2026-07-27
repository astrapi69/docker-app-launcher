"""Tests for :mod:`docker_app_launcher.docker.build_readiness` (#54).

The capability gate: present is not functional. Covers the device case
(plugin present, buildx 0.8.2, build impossible), the COLLECTING behaviour
(several links reported in one run), the only-raise rule for app-declared
minimums, and source attribution (app vs launcher).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from packaging.version import Version

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import build_readiness, py_client
from docker_app_launcher.docker.tool_versions import ToolVersions


def _tv(*, engine: str = "27.5.1", api: str = "1.47", compose: str = "2.40.2", buildx: str = "0.20.0") -> ToolVersions:
    def v(x: str) -> Version | None:
        return Version(x) if x else None

    return ToolVersions(
        engine_raw=engine or "",
        engine=v(engine),
        api_raw=api or "",
        api=v(api),
        compose_raw=compose or "",
        compose=v(compose),
        buildx_raw=buildx or "",
        buildx=v(buildx),
    )


@pytest.fixture
def cconfig(config: LauncherConfig) -> LauncherConfig:
    """A compose-mode config whose compose file actually exists on disk."""
    config.compose_path.parent.mkdir(parents=True, exist_ok=True)
    config.compose_path.write_text("services: {}\n")
    return config


def _pin(
    monkeypatch: pytest.MonkeyPatch, tv: ToolVersions, *, frontend: tuple[str, str] = ("plugin", "plugin")
) -> None:
    monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: tv)
    monkeypatch.setattr(
        build_readiness,
        "compose_available",
        lambda c: (frontend[0] in ("plugin", "legacy"), frontend[1]),
    )


class TestComposeGate:
    def test_modern_toolchain_is_ready(self, cconfig, monkeypatch) -> None:
        _pin(monkeypatch, _tv(compose="2.40.2", buildx="0.20.0"))
        assert build_readiness.compose_blockers(cconfig) == []

    def test_old_buildx_blocks_with_actionable_message(self, cconfig, monkeypatch) -> None:
        # The device case: plugin present, buildx 0.8.2.
        _pin(monkeypatch, _tv(compose="2.40.2", buildx="0.8.2"))
        blockers = build_readiness.compose_blockers(cconfig)
        assert len(blockers) == 1
        msg = blockers[0]
        assert "buildx" in msg and "0.17" in msg and "0.8.2" in msg
        assert build_readiness.BUILDX_PLUGIN_PATH in msg  # the portable install path

    def test_missing_buildx_blocks(self, cconfig, monkeypatch) -> None:
        _pin(monkeypatch, _tv(compose="2.40.2", buildx=""))
        blockers = build_readiness.compose_blockers(cconfig)
        assert len(blockers) == 1 and "buildx" in blockers[0]
        assert build_readiness.BUILDX_PLUGIN_PATH in blockers[0]

    def test_old_compose_does_not_gate_buildx(self, cconfig, monkeypatch) -> None:
        # Compose below 2.40.2 does not emit the buildx error - do not invent it.
        _pin(monkeypatch, _tv(compose="2.39.0", buildx="0.8.2"))
        assert build_readiness.compose_blockers(cconfig) == []

    def test_legacy_v1_never_gates_buildx(self, cconfig, monkeypatch) -> None:
        # Legacy v1 does not use bake, so an old buildx is irrelevant there.
        _pin(monkeypatch, _tv(compose="1.29.2", buildx="0.8.2"), frontend=("legacy", "legacy"))
        assert build_readiness.compose_blockers(cconfig) == []

    def test_collects_file_and_buildx_in_one_run(self, config, monkeypatch) -> None:
        # No compose file AND an old buildx: BOTH must surface in one message.
        _pin(monkeypatch, _tv(compose="2.40.2", buildx="0.8.2"))
        blockers = build_readiness.compose_blockers(config)  # no _make_repo -> file missing
        assert len(blockers) == 2
        joined = build_readiness.join_blockers(blockers)
        assert "Compose file not found" in joined and "buildx" in joined

    def test_compose_missing_reported_without_buildx_guess(self, cconfig, monkeypatch) -> None:
        _pin(monkeypatch, _tv(compose="", buildx="0.8.2"), frontend=("none", "none"))
        blockers = build_readiness.compose_blockers(cconfig)
        assert any("Compose is not available" in b for b in blockers)
        assert not any("buildx" in b for b in blockers), "no buildx claim when compose version is unknown"


class TestAppDeclaredRequirements:
    def test_app_engine_floor_attributed_to_the_app(self, cconfig, monkeypatch) -> None:
        cconfig.min_engine_version = "25.0"
        _pin(monkeypatch, _tv(engine="20.10.21", compose="2.40.2", buildx="0.20.0"))
        blockers = build_readiness.compose_blockers(cconfig)
        assert len(blockers) == 1
        assert "This app requires" in blockers[0] and "engine" in blockers[0] and "25.0" in blockers[0]

    def test_app_api_floor_uses_api_version(self, cconfig, monkeypatch) -> None:
        cconfig.min_api_version = "1.50"
        _pin(monkeypatch, _tv(api="1.47", compose="2.40.2", buildx="0.20.0"))
        blockers = build_readiness.compose_blockers(cconfig)
        assert len(blockers) == 1 and "api" in blockers[0] and "1.50" in blockers[0]

    def test_config_can_raise_buildx_above_intrinsic(self, cconfig, monkeypatch) -> None:
        cconfig.min_buildx_version = "0.20"
        _pin(monkeypatch, _tv(compose="2.40.2", buildx="0.18.0"))  # satisfies 0.17, not 0.20
        blockers = build_readiness.compose_blockers(cconfig)
        assert len(blockers) == 1 and "0.20" in blockers[0]

    def test_config_cannot_lower_below_intrinsic(self, cconfig, monkeypatch, caplog) -> None:
        # Declaring a buildx floor BELOW 0.17 must not weaken the gate: buildx
        # 0.12 stays blocked, and the intrinsic 0.17 is what is demanded.
        cconfig.min_buildx_version = "0.10"
        _pin(monkeypatch, _tv(compose="2.40.2", buildx="0.12.0"))
        with caplog.at_level("WARNING"):
            blockers = build_readiness.compose_blockers(cconfig)
        assert len(blockers) == 1 and "0.17" in blockers[0]
        assert any("below the launcher's intrinsic" in r.message for r in caplog.records)

    def test_config_below_intrinsic_still_passes_when_intrinsic_met(self, cconfig, monkeypatch) -> None:
        # buildx 0.18 meets the intrinsic 0.17; a config floor of 0.10 is moot.
        cconfig.min_buildx_version = "0.10"
        _pin(monkeypatch, _tv(compose="2.40.2", buildx="0.18.0"))
        assert build_readiness.compose_blockers(cconfig) == []


class TestDockerfileGate:
    @pytest.fixture
    def dconfig(self, config: LauncherConfig, tmp_path: Path) -> LauncherConfig:
        config.deployment_mode = "dockerfile"
        config.install_dir = str(tmp_path / "repo")
        config.resolve()
        config.build_context_path.mkdir(parents=True, exist_ok=True)
        config.dockerfile_path.write_text("FROM scratch\n")
        return config

    def test_ready_when_everything_present(self, dconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: _tv())
        assert build_readiness.dockerfile_blockers(dconfig) == []

    def test_missing_dockerpy_and_context_collected(
        self, dconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(py_client, "available", lambda: False)
        monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: _tv())
        dconfig.dockerfile_path.unlink()
        blockers = build_readiness.dockerfile_blockers(dconfig)
        assert any("docker-py" in b for b in blockers)
        assert any("Dockerfile" in b for b in blockers)

    def test_no_buildx_gate_in_dockerfile_mode(self, dconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        # buildx is irrelevant to the classic docker-py builder.
        monkeypatch.setattr(py_client, "available", lambda: True)
        monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: _tv(buildx="0.8.2"))
        assert build_readiness.dockerfile_blockers(dconfig) == []

    def test_app_engine_floor_applies(self, dconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        dconfig.min_engine_version = "25.0"
        monkeypatch.setattr(py_client, "available", lambda: True)
        monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: _tv(engine="20.10.21"))
        blockers = build_readiness.dockerfile_blockers(dconfig)
        assert any("engine" in b and "25.0" in b for b in blockers)


class TestBaseUnresolved:
    """G3 (#64): a missing compose file / Dockerfile under the CWD fallback is
    reported loudly with install_dir guidance, not a bare 'not found'."""

    def test_compose_base_unresolved_advises_install_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = LauncherConfig(app_name="X").resolve()  # no install_dir -> cwd fallback
        assert cfg.base_is_cwd_fallback is True
        _pin(monkeypatch, _tv())
        blockers = build_readiness.compose_blockers(cfg)
        assert any("install_dir" in b for b in blockers), blockers

    def test_compose_not_found_stays_plain_with_install_dir(
        self, cconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # install_dir IS set (cconfig has it) but delete the compose file.
        cconfig.compose_path.unlink()
        _pin(monkeypatch, _tv())
        blockers = build_readiness.compose_blockers(cconfig)
        assert any("Compose file not found" in b for b in blockers)
        assert not any("install_dir" in b for b in blockers)

    def test_dockerfile_base_unresolved_advises_install_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = LauncherConfig(app_name="X", deployment_mode="dockerfile").resolve()
        assert cfg.base_is_cwd_fallback is True
        monkeypatch.setattr(py_client, "available", lambda: True)
        monkeypatch.setattr(build_readiness, "detect_tool_versions", lambda c: _tv())
        blockers = build_readiness.dockerfile_blockers(cfg)
        assert any("install_dir" in b for b in blockers), blockers


class TestDiskPreflight:
    """G4 (#61): an advisory disk-space check before the build."""

    def _free(self, monkeypatch: pytest.MonkeyPatch, free: int) -> None:
        import collections
        import shutil

        usage = collections.namedtuple("usage", "total used free")
        monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(0, 0, free))

    def test_low_disk_is_flagged(self, cconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        _pin(monkeypatch, _tv())
        self._free(monkeypatch, 100_000_000)  # 100 MB, below the 2 GB floor
        blockers = build_readiness.compose_blockers(cconfig)
        assert any("disk" in b.lower() for b in blockers), blockers

    def test_ample_disk_is_not_flagged(self, cconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        _pin(monkeypatch, _tv())
        self._free(monkeypatch, 50_000_000_000)  # 50 GB
        assert build_readiness.compose_blockers(cconfig) == []

    def test_disk_check_disabled_by_zero(self, cconfig: LauncherConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        cconfig.min_build_disk_bytes = 0
        _pin(monkeypatch, _tv())
        self._free(monkeypatch, 1)
        assert build_readiness.compose_blockers(cconfig) == []
