"""Tests for the structured diagnosis reports and their CLI rendering (#86)."""

from __future__ import annotations

import json

import pytest

from docker_app_launcher import __main__, check_ids, doctor
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.diagnostics_report import (
    CheckResult,
    DoctorReport,
    HealthReport,
    StatusReport,
    SupportBundle,
    visible_env_keys,
)
from docker_app_launcher.docker.tool_versions import ToolVersions

# The stable check-id API (#86) now lives in the SHIPPED package
# (src/docker_app_launcher/check_ids.py, #81): an interface consumers parse
# cannot live where shipped code cannot import it. Re-exported here so the
# existing importers keep working AND so this stops being an independent
# copy that can drift - the whole point of the move.
KNOWN_CHECK_IDS = set(check_ids.KNOWN_CHECK_IDS)


@pytest.fixture
def dconfig(tmp_path):
    cfg = LauncherConfig(
        app_name="Diag App",
        container_name="diag-app",
        default_port=8080,
        install_dir=str(tmp_path / "repo"),
        config_dir=str(tmp_path / ".diag-app"),
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


class TestCheckResult:
    def test_rejects_unknown_status(self) -> None:
        with pytest.raises(ValueError, match="status"):
            CheckResult("x", "great", "msg")

    def test_fix_only_present_when_set(self) -> None:
        assert "fix" not in CheckResult("x", "ok", "m").to_dict()
        assert CheckResult("x", "error", "m", fix="do this").to_dict()["fix"] == "do this"


class TestDoctorReportObject:
    def test_ids_are_from_the_registered_set(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch, state="running")
        monkeypatch.setattr(doctor, "_published_ports_of_running", lambda c: "0.0.0.0:9000->80/tcp")
        report = doctor.collect_doctor_report(dconfig)
        unknown = {c.id for c in report.checks} - KNOWN_CHECK_IDS
        assert not unknown, f"unregistered check id(s) {unknown} - ids are API, register consciously"

    def test_json_shape(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch)
        data = doctor.collect_doctor_report(dconfig).to_dict()
        assert data["ok"] is True and data["complete"] is True and data["problems"] == 0
        assert all({"id", "status", "message"} <= set(c) for c in data["checks"])

    def test_daemon_down_marks_incomplete(self, dconfig, monkeypatch) -> None:
        monkeypatch.setattr(doctor, "check_docker", lambda: (False, "Docker is not started."))
        report = doctor.collect_doctor_report(dconfig)
        assert report.complete is False
        assert report.to_dict()["ok"] is False, "an incomplete pass must never read as ok"

    def test_text_renders_from_the_object(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch)
        report = doctor.collect_doctor_report(dconfig)
        text = doctor.render_doctor_text(report)
        assert "0 problem(s)" in text and "ready to build" in text


class TestStatusReportObject:
    def test_running_but_unhealthy_is_visible(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch, state="running")
        monkeypatch.setattr(doctor, "health_check", lambda c, port=None: (False, "no route"))
        status = doctor.collect_status_report(dconfig)
        assert status.to_dict()["health"] == {"ok": False, "detail": "no route"}
        assert "FAILED" in status.to_text(), "running-but-broken must never read as a plain running"

    def test_not_running_has_no_health(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch, state="stopped")
        status = doctor.collect_status_report(dconfig)
        assert status.health_ok is None and status.to_dict()["health"] is None
        assert status.to_text() == "Status: stopped"


class TestSupportBundleObject:
    def test_contents_stated_first(self) -> None:
        bundle = SupportBundle(fields={"app": "X"})
        lines = bundle.to_text().splitlines()
        assert lines[0] == "docker-app-launcher support bundle"
        assert lines[1].startswith("contains: "), "the bundle says FIRST what it contains"
        assert "NO environment values" in lines[1]

    def test_secret_filter_on_env_keys(self) -> None:
        env = {"PLAIN_FLAG": "1", "MY_API_TOKEN": "sekrit-123", "DB_PASSWORD": "hunter2", "AUTH_HEADER": "x"}
        keys = visible_env_keys(env)
        assert keys == ["PLAIN_FLAG"], f"secret-looking names must be withheld, got {keys}"

    def test_bundle_never_contains_env_values(self, dconfig, monkeypatch) -> None:
        _healthy_world(monkeypatch, state="running")
        dconfig.container_env = {"MY_API_TOKEN": "sekrit-123", "APP_FLAG": "flagvalue-456"}
        bundle = doctor.collect_support_bundle(dconfig)
        for rendered in (bundle.to_text(), json.dumps(bundle.to_dict())):
            assert "sekrit-123" not in rendered, "env VALUES must never appear"
            assert "flagvalue-456" not in rendered, "env VALUES must never appear - not even harmless ones"
            assert "MY_API_TOKEN" not in rendered, "secret-looking key names are withheld"
        assert "APP_FLAG" in bundle.to_text(), "harmless key NAMES are listed for diagnosis"

    def test_bundle_reads_image_identity_from_the_manifest(self, tmp_path, monkeypatch) -> None:
        cfg = LauncherConfig(
            app_name="Img",
            deployment_mode="image",
            image_reference="ghcr.io/o/a:1",
            install_dir=str(tmp_path),
            config_dir=str(tmp_path / ".img"),
            locale="en",
        ).resolve()
        _healthy_world(monkeypatch)
        cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.manifest_path.write_text(
            json.dumps({"image_reference": "ghcr.io/o/a:1", "image_id": "sha256:abc", "image_source": "registry"}),
            encoding="utf-8",
        )
        bundle = doctor.collect_support_bundle(cfg)
        assert bundle.fields["image_id"] == "sha256:abc", "identity comes FROM the manifest (#80)"


class TestCliFlags:
    def _cfg_file(self, dconfig, tmp_path) -> str:
        path = tmp_path / "launcher.json"
        dconfig.to_json(path)
        return str(path)

    def test_doctor_json_is_parseable_and_exits_by_verdict(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        report = DoctorReport(app_name="X", mode="compose", checks=[CheckResult("docker_running", "error", "down")])
        monkeypatch.setattr(doctor, "collect_doctor_report", lambda c: report)
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--doctor", "--json"])
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is False and data["checks"][0]["id"] == "docker_running"

    def test_status_json(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        status = StatusReport(app_name="X", mode="compose", state="stopped", port=8080, url="http://localhost:8080/")
        monkeypatch.setattr(doctor, "collect_status_report", lambda c: status)
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--status", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["state"] == "stopped"

    def test_health_exits_by_verdict(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        monkeypatch.setattr(doctor, "collect_health_report", lambda c: HealthReport(False, "no route", "u"))
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--health"])
        assert rc == 1 and "no route" in capsys.readouterr().out

    def test_health_json(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        monkeypatch.setattr(doctor, "collect_health_report", lambda c: HealthReport(True, "healthy", "u"))
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--health", "--json"])
        assert rc == 0 and json.loads(capsys.readouterr().out) == {"ok": True, "detail": "healthy", "url": "u"}

    def test_app_logs_prints_tail(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        from docker_app_launcher import actions

        monkeypatch.setattr(actions, "app_logs", lambda c: (True, "log tail here"))
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--app-logs"])
        assert rc == 0 and "log tail here" in capsys.readouterr().out

    def test_support_bundle_text_and_json(self, dconfig, monkeypatch, tmp_path, capsys) -> None:
        monkeypatch.setattr(doctor, "collect_support_bundle", lambda c: SupportBundle(fields={"app": "X"}))
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--support-bundle"])
        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("docker-app-launcher support bundle")
        rc = __main__.main(["--config", self._cfg_file(dconfig, tmp_path), "--support-bundle", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["app"] == "X"

    def test_wrong_config_path_still_exits_2(self, tmp_path, capsys) -> None:
        rc = __main__.main(["--config", str(tmp_path / "missing.json"), "--doctor"])
        assert rc == 2, "config errors are exit code 2 (usage/config contract)"
