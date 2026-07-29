"""Tests for the --doctor diagnostic pass (#75, #76) - everything mocked."""

from __future__ import annotations

import pytest

from docker_app_launcher import __main__, doctor
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker.tool_versions import ToolVersions


@pytest.fixture
def dconfig(tmp_path):
    cfg = LauncherConfig(
        app_name="Doc App",
        container_name="doc-app",
        default_port=8080,
        install_dir=str(tmp_path / "repo"),
        config_dir=str(tmp_path / ".doc-app"),
        locale="en",
    ).resolve()
    cfg.compose_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.compose_path.write_text("services: {}\n", encoding="utf-8")
    return cfg


def _healthy_world(monkeypatch, *, state: str = "not_installed") -> None:
    monkeypatch.setattr(doctor, "check_docker", lambda: (True, "Docker is running."))
    monkeypatch.setattr(doctor, "detect_tool_versions", lambda c: ToolVersions())
    monkeypatch.setattr("docker_app_launcher.docker.build_readiness.compose_blockers", lambda c: [])
    monkeypatch.setattr(doctor, "get_state", lambda c: state)
    monkeypatch.setattr(doctor, "health_check", lambda c, port=None: (True, "healthy"))


class TestDoctor:
    def test_all_green_when_ready(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch)
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is True
        assert "0 problem(s)" in report
        assert "ready to build" in report

    def test_docker_down_short_circuits(self, dconfig, monkeypatch) -> None:
        monkeypatch.setattr(doctor, "check_docker", lambda: (False, "Docker is not started."))
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is False
        assert "Docker is not started" in report
        assert "readiness" not in report, "no readiness probing against a dead daemon"

    def test_blockers_are_listed(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch)
        monkeypatch.setattr(
            "docker_app_launcher.docker.build_readiness.compose_blockers", lambda c: ["blocker one", "blocker two"]
        )
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is False
        assert "blocker one" in report and "blocker two" in report
        assert "2 problem(s)" in report

    def test_missing_compose_file_is_a_problem(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch)
        dconfig.compose_path.unlink()
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is False and str(dconfig.compose_path) in report

    def test_running_healthy_reports_health_url(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch, state="running")
        monkeypatch.setattr(doctor, "_published_ports_of_running", lambda c: "0.0.0.0:8080->8000/tcp")
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is True
        assert "http://localhost:8080/api/health" in report

    def test_running_port_drift_is_flagged(self, dconfig, monkeypatch) -> None:
        # #76: a running container that publishes a DIFFERENT port than the
        # launcher expects must be called out - plain --status hid this.
        _healthy_world(monkeypatch, state="running")
        monkeypatch.setattr(doctor, "_published_ports_of_running", lambda c: "0.0.0.0:9000->8000/tcp")
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is False
        assert "port drift" in report and "9000" in report

    def test_running_unhealthy_is_a_problem(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch, state="running")
        monkeypatch.setattr(doctor, "_published_ports_of_running", lambda c: "")
        monkeypatch.setattr(doctor, "health_check", lambda c, port=None: (False, "no route"))
        healthy, report = doctor.run_doctor(dconfig)
        assert healthy is False and "no route" in report

    def test_dockerfile_mode_uses_dockerfile_blockers(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch)
        dconfig.deployment_mode = "dockerfile"
        dconfig.dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
        dconfig.dockerfile_path.write_text("FROM scratch\n", encoding="utf-8")
        called: list[str] = []

        def record_blockers(c: object) -> list[str]:
            called.append("df")
            return []

        monkeypatch.setattr("docker_app_launcher.docker.build_readiness.dockerfile_blockers", record_blockers)
        healthy, _ = doctor.run_doctor(dconfig)
        assert healthy is True and called == ["df"]


class TestDoctorCli:
    def test_flag_routes_and_exits_by_verdict(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        from docker_app_launcher.diagnostics_report import CheckResult, DoctorReport

        dconfig.to_json(tmp_path / "launcher.json")
        checks = [CheckResult("docker_running", "error", "the report")]
        report = DoctorReport(app_name="X", mode="compose", checks=checks)
        monkeypatch.setattr(doctor, "collect_doctor_report", lambda c: report)
        rc = __main__.main(["--config", str(tmp_path / "launcher.json"), "--doctor"])
        assert rc == 1
        assert "the report" in capsys.readouterr().out


class TestDoctorImageMode:
    """--doctor line for the image deployment mode (#78)."""

    @pytest.fixture
    def pconfig(self, tmp_path):
        return LauncherConfig(
            app_name="Image App",
            deployment_mode="image",
            image_reference="ghcr.io/owner/app:2.0.0",
            default_port=8080,
            install_dir=str(tmp_path / "repo"),
            config_dir=str(tmp_path / ".image-app"),
            locale="en",
        ).resolve()

    def _image_world(self, monkeypatch, blockers: list[str] | None = None) -> None:
        monkeypatch.setattr(doctor, "check_docker", lambda: (True, "Docker is running."))
        monkeypatch.setattr(doctor, "detect_tool_versions", lambda c: ToolVersions())
        monkeypatch.setattr("docker_app_launcher.docker.build_readiness.image_blockers", lambda c: list(blockers or []))
        monkeypatch.setattr(doctor, "get_state", lambda c: "not_installed")
        monkeypatch.setattr(doctor, "health_check", lambda c, port=None: (True, "healthy"))

    def test_image_reference_line_and_green(self, pconfig, monkeypatch) -> None:
        self._image_world(monkeypatch)
        healthy, report = doctor.run_doctor(pconfig)
        assert healthy is True
        assert "ghcr.io/owner/app:2.0.0" in report
        assert "0 problem(s)" in report

    def test_image_blockers_are_dispatched(self, pconfig, monkeypatch) -> None:
        self._image_world(monkeypatch, blockers=["image blocker"])
        healthy, report = doctor.run_doctor(pconfig)
        assert healthy is False and "image blocker" in report

    def test_compose_blockers_never_consulted(self, pconfig, monkeypatch) -> None:
        self._image_world(monkeypatch)

        def boom(c: object) -> list[str]:
            raise AssertionError("compose_blockers must not run in image mode")

        monkeypatch.setattr("docker_app_launcher.docker.build_readiness.compose_blockers", boom)
        healthy, _ = doctor.run_doctor(pconfig)
        assert healthy is True

    def test_missing_install_dir_is_informational_only(self, pconfig, monkeypatch) -> None:
        # Image mode needs no source tree: an absent install_dir must not
        # count as a problem (it is where the archive MAY live, nothing more).
        self._image_world(monkeypatch)
        healthy, report = doctor.run_doctor(pconfig)
        assert healthy is True and "0 problem(s)" in report

    def test_no_image_source_is_a_problem(self, pconfig, monkeypatch) -> None:
        self._image_world(monkeypatch)
        pconfig.image_reference = ""
        healthy, _ = doctor.run_doctor(pconfig)
        assert healthy is False
