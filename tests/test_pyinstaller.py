"""Tests for the PyInstaller integration helpers."""

from __future__ import annotations

import pytest

from docker_app_launcher import pyinstaller
from docker_app_launcher.pyinstaller import build_info


class TestHiddenImports:
    def test_lists_all_submodules(self) -> None:
        imports = build_info.hidden_imports()
        for module in (
            "docker_app_launcher.actions",
            "docker_app_launcher.config",
            "docker_app_launcher.gui",
            "docker_app_launcher.i18n",
            "docker_app_launcher.lockfile",
            "docker_app_launcher.logging_setup",
            "docker_app_launcher.tray",
            "docker_app_launcher.update_check",
        ):
            assert module in imports

    def test_returns_a_fresh_list(self) -> None:
        build_info.hidden_imports().append("junk")
        assert "junk" not in build_info.hidden_imports()


class TestBuildInfo:
    def test_write_then_read_roundtrip(self, tmp_path) -> None:
        dest = tmp_path / "pkg" / "_build_info.py"
        build_info.write_build_info(dest, "1.95.0")
        assert dest.is_file()
        assert build_info.read_build_info(dest) == "1.95.0"

    def test_read_absent_is_none(self, tmp_path) -> None:
        assert build_info.read_build_info(tmp_path / "missing.py") is None

    def test_read_malformed_is_none(self, tmp_path) -> None:
        path = tmp_path / "_build_info.py"
        path.write_text("# no version here\n", encoding="utf-8")
        assert build_info.read_build_info(path) is None


class TestRenderSpec:
    def test_template_file_exists(self) -> None:
        assert pyinstaller.spec_template_path().is_file()

    def test_renders_all_markers(self) -> None:
        spec = pyinstaller.render_spec(
            app_slug="adaptive-learner",
            entry_script="run_launcher.py",
            icon_path="adaptive-learner.png",
            config_json="launcher.json",
        )
        assert "{{" not in spec and "}}" not in spec
        assert 'name="adaptive-learner"' in spec
        assert '"run_launcher.py"' in spec
        assert "adaptive-learner.png" in spec
        assert "launcher.json" in spec
        assert "hidden_imports()" in spec

    def test_missing_field_raises(self) -> None:
        with pytest.raises(TypeError):
            pyinstaller.render_spec(app_slug="x", entry_script="y", icon_path="z")  # type: ignore[call-arg]
