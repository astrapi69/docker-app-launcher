"""Tests for the frontend registry (swappable GUI backends)."""

from __future__ import annotations

import types

import pytest

from docker_app_launcher import frontends, gui
from docker_app_launcher.config import LauncherConfig


class TestBuiltins:
    def test_tk_resolves_to_the_gui_module(self) -> None:
        assert frontends.get_frontend("tk") is gui

    def test_tk_module_satisfies_the_contract(self) -> None:
        assert callable(frontends.get_frontend("tk").run)

    def test_available_contains_tk(self) -> None:
        assert "tk" in frontends.available_frontends()


class TestResolution:
    def test_unknown_name_lists_known_frontends(self) -> None:
        with pytest.raises(ValueError, match=r"unknown gui_backend 'web'.*tk"):
            frontends.get_frontend("web")

    def test_entry_point_frontend_is_loaded(self, monkeypatch) -> None:
        fake_module = types.ModuleType("fake_web")
        fake_module.run = lambda config, *, debug=False: 0  # type: ignore[attr-defined]

        class _FakeEp:
            name = "web"

            def load(self):
                return fake_module

        monkeypatch.setattr(
            frontends, "entry_points", lambda group: [_FakeEp()] if group == frontends.ENTRY_POINT_GROUP else []
        )
        assert frontends.get_frontend("web") is fake_module
        assert "web" in frontends.available_frontends()

    def test_builtin_wins_over_entry_point_of_same_name(self, monkeypatch) -> None:
        class _FakeEp:
            name = "tk"

            def load(self):  # pragma: no cover - must never be called
                raise AssertionError("builtin must win")

        monkeypatch.setattr(frontends, "entry_points", lambda group: [_FakeEp()])
        assert frontends.get_frontend("tk") is gui

    def test_frontend_without_run_is_rejected(self, monkeypatch) -> None:
        broken = types.ModuleType("broken_frontend")

        class _FakeEp:
            name = "broken"

            def load(self):
                return broken

        monkeypatch.setattr(frontends, "entry_points", lambda group: [_FakeEp()])
        with pytest.raises(TypeError, match="no callable run"):
            frontends.get_frontend("broken")


class TestConfigField:
    def test_default_backend_is_tk(self) -> None:
        assert LauncherConfig(app_name="X").gui_backend == "tk"

    def test_backend_survives_serialization_roundtrip(self, tmp_path) -> None:
        import json

        config = LauncherConfig(app_name="X", gui_backend="qt")
        path = tmp_path / "launcher.json"
        config.to_json(path)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded.get("gui_backend") == "qt"


class TestLaunchRouting:
    def test_launch_uses_the_configured_backend(self, monkeypatch) -> None:
        import docker_app_launcher

        received: list[str] = []
        fake = types.ModuleType("fake_frontend")

        def fake_run(config, *, debug=False):
            received.append(config.gui_backend)
            return 7

        fake.run = fake_run  # type: ignore[attr-defined]

        def fake_get(name):
            received.append(f"resolved:{name}")
            return fake

        monkeypatch.setattr(frontends, "get_frontend", fake_get)
        code = docker_app_launcher.launch(app_name="X", gui_backend="fancy")
        assert code == 7
        assert received[0] == "resolved:fancy"
