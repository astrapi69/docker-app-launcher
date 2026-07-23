"""Tests for the package-level ``launch()`` convenience API (GUI mocked)."""

from __future__ import annotations

import docker_app_launcher
from docker_app_launcher import gui
from docker_app_launcher.config import LauncherConfig


class TestLaunch:
    def test_launch_with_explicit_config(self, monkeypatch) -> None:
        received: list[LauncherConfig] = []

        def fake_run(config: LauncherConfig, **k: object) -> int:
            received.append(config)
            return 0

        monkeypatch.setattr(gui, "run", fake_run)
        config = LauncherConfig(app_name="My App", default_port=8080)
        assert docker_app_launcher.launch(config) == 0
        assert received[0] is config
        assert received[0].app_slug  # resolve() ran

    def test_launch_builds_config_from_kwargs(self, monkeypatch) -> None:
        received: list[LauncherConfig] = []

        def fake_run(config: LauncherConfig, **k: object) -> int:
            received.append(config)
            return 0

        monkeypatch.setattr(gui, "run", fake_run)
        assert docker_app_launcher.launch(app_name="Kw App", default_port=9090) == 0
        assert received[0].app_name == "Kw App"
        assert received[0].default_port == 9090

    def test_launch_propagates_exit_code(self, monkeypatch) -> None:
        monkeypatch.setattr(gui, "run", lambda config, **k: 3)
        assert docker_app_launcher.launch(app_name="X") == 3
