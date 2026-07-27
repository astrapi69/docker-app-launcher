"""Tests for Snap-confinement detection and surfacing (G7, #63)."""

from __future__ import annotations

import logging

import pytest

from docker_app_launcher import snap


class TestIsSnapConfined:
    def test_false_without_snap_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SNAP", raising=False)
        monkeypatch.delenv("SNAP_NAME", raising=False)
        assert snap.is_snap_confined() is False

    def test_true_with_snap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNAP", "/snap/docker-app-launcher/42")
        assert snap.is_snap_confined() is True

    def test_true_with_snap_name_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SNAP", raising=False)
        monkeypatch.setenv("SNAP_NAME", "docker-app-launcher")
        assert snap.is_snap_confined() is True


class TestLogConfinementWarning:
    def test_no_warning_when_not_confined(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        monkeypatch.delenv("SNAP", raising=False)
        monkeypatch.delenv("SNAP_NAME", raising=False)
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.snap"):
            fired = snap.log_confinement_warning()
        assert fired is False
        assert not caplog.records

    def test_warns_once_when_confined(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        monkeypatch.setenv("SNAP", "/snap/app/7")
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.snap"):
            fired = snap.log_confinement_warning()
        assert fired is True
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "Snap confinement" in joined and "install_dir" in joined

    def test_main_logs_warning_under_snap(self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        from docker_app_launcher import __main__

        monkeypatch.setenv("SNAP", "/snap/app/7")
        cfg = tmp_path / "launcher.json"
        cfg.write_text('{"app_name": "X"}', encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.snap"):
            __main__.main(["--config", str(cfg), "--status"])
        assert any("Snap confinement" in r.getMessage() for r in caplog.records)
