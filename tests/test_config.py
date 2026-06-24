"""Tests for LauncherConfig: derivation, paths, filters, (de)serialization."""

from __future__ import annotations

import json
from pathlib import Path

from docker_app_launcher import actions, gui
from docker_app_launcher.config import LauncherConfig, slugify


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("My App") == "my-app"

    def test_strips_punctuation(self) -> None:
        assert slugify("Hello, World!") == "hello-world"

    def test_collapses_separators(self) -> None:
        assert slugify("a   b___c") == "a-b-c"

    def test_trims_edges(self) -> None:
        assert slugify("  __Edge__  ") == "edge"

    def test_numbers_kept(self) -> None:
        assert slugify("App 2 Go") == "app-2-go"


class TestResolve:
    def test_slug_from_name(self) -> None:
        cfg = LauncherConfig(app_name="My Cool App").resolve()
        assert cfg.app_slug == "my-cool-app"

    def test_container_image_project_default_to_slug(self) -> None:
        cfg = LauncherConfig(app_name="My App").resolve()
        assert cfg.container_name == "my-app"
        assert cfg.image_name == "my-app"
        assert cfg.compose_project == "my-app"

    def test_explicit_values_preserved(self) -> None:
        cfg = LauncherConfig(app_name="My App", container_name="custom", image_name="img").resolve()
        assert cfg.container_name == "custom"
        assert cfg.image_name == "img"

    def test_config_dir_default(self) -> None:
        cfg = LauncherConfig(app_name="My App").resolve()
        assert cfg.config_dir.endswith(".my-app")

    def test_releases_url_from_repo(self) -> None:
        cfg = LauncherConfig(app_name="X", repo_url="https://github.com/o/r").resolve()
        assert cfg.releases_url == "https://github.com/o/r/releases/latest"

    def test_releases_url_trailing_slash(self) -> None:
        cfg = LauncherConfig(app_name="X", repo_url="https://github.com/o/r/").resolve()
        assert cfg.releases_url == "https://github.com/o/r/releases/latest"

    def test_no_repo_no_releases(self) -> None:
        cfg = LauncherConfig(app_name="X").resolve()
        assert cfg.releases_url == ""

    def test_idempotent(self) -> None:
        cfg = LauncherConfig(app_name="My App").resolve()
        snapshot = (cfg.app_slug, cfg.container_name, cfg.config_dir)
        cfg.resolve()
        assert (cfg.app_slug, cfg.container_name, cfg.config_dir) == snapshot

    def test_returns_self(self) -> None:
        cfg = LauncherConfig(app_name="X")
        assert cfg.resolve() is cfg


class TestPaths:
    def test_manifest_path(self, tmp_path: Path) -> None:
        cfg = LauncherConfig(app_name="X", config_dir=str(tmp_path), manifest_file="m.json").resolve()
        assert cfg.manifest_path == tmp_path / "m.json"

    def test_launcher_config_file(self, tmp_path: Path) -> None:
        cfg = LauncherConfig(app_name="X", config_dir=str(tmp_path)).resolve()
        assert cfg.launcher_config_file == tmp_path / "launcher.json"

    def test_compose_path_relative_to_install_dir(self, tmp_path: Path) -> None:
        cfg = LauncherConfig(app_name="X", install_dir=str(tmp_path), compose_file="dc.yml").resolve()
        assert cfg.compose_path == tmp_path / "dc.yml"

    def test_compose_path_absolute(self, tmp_path: Path) -> None:
        absolute = tmp_path / "abs.yml"
        cfg = LauncherConfig(app_name="X", compose_file=str(absolute)).resolve()
        assert cfg.compose_path == absolute


class TestFilters:
    def test_name_filters_includes_legacy(self) -> None:
        cfg = LauncherConfig(app_name="X", container_name="x", legacy_names=["old", "older"]).resolve()
        assert cfg.name_filters() == ["x", "old", "older"]

    def test_image_patterns(self) -> None:
        cfg = LauncherConfig(app_name="X", image_name="img", legacy_names=["old"]).resolve()
        assert cfg.image_patterns() == ["img", "old"]

    def test_cleanup_patterns_deduped(self) -> None:
        cfg = LauncherConfig(app_name="X", container_name="x", image_name="x", legacy_names=["x", "y"]).resolve()
        assert cfg.cleanup_patterns() == ["x", "y"]

    def test_filters_skip_empty(self) -> None:
        cfg = LauncherConfig(app_name="X", container_name="x", legacy_names=["", "y"]).resolve()
        assert cfg.name_filters() == ["x", "y"]


class TestSerialization:
    def test_to_json_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        cfg = LauncherConfig(app_name="My App", default_port=9000).resolve()
        cfg.to_json(path)
        loaded = LauncherConfig.from_json(path)
        assert loaded.app_name == "My App"
        assert loaded.default_port == 9000

    def test_internal_port_defaults_empty(self) -> None:
        cfg = LauncherConfig(app_name="X").resolve()
        assert cfg.internal_ports == {}
        assert cfg.env_internal_port_keys == {}
        assert cfg.show_advanced_ports is False

    def test_internal_ports_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        cfg = LauncherConfig(
            app_name="X",
            internal_ports={"backend": 8000, "nginx": 80},
            env_internal_port_keys={"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"},
            show_advanced_ports=True,
        ).resolve()
        cfg.to_json(path)
        loaded = LauncherConfig.from_json(path)
        assert loaded.internal_ports == {"backend": 8000, "nginx": 80}
        assert loaded.env_internal_port_keys == {"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"}
        assert loaded.show_advanced_ports is True

    def test_to_json_excludes_callbacks(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        cfg = LauncherConfig(app_name="X", on_error=lambda *a: None).resolve()
        cfg.to_json(path)
        data = json.loads(path.read_text())
        assert "on_error" not in data

    def test_from_json_missing_file_defaults(self, tmp_path: Path) -> None:
        cfg = LauncherConfig.from_json(tmp_path / "nope.json")
        assert cfg.app_name == "My App"
        assert cfg.app_slug == "my-app"  # resolved

    def test_from_json_ignores_unknown_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"app_name": "Y", "totally_unknown": 1}))
        cfg = LauncherConfig.from_json(path)
        assert cfg.app_name == "Y"

    def test_from_json_is_resolved(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"app_name": "Resolve Me"}))
        cfg = LauncherConfig.from_json(path)
        assert cfg.container_name == "resolve-me"

    def test_to_json_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "cfg.json"
        LauncherConfig(app_name="X").resolve().to_json(path)
        assert path.is_file()


class TestMinimalConfigSmoke:
    """An app must run from a minimal config - only ``app_name`` (#6 / #1054).

    Guards the package's "fully configuration-driven, nothing hard-coded"
    property: defaults resolve to sensible values and the helper layer the GUI +
    CLI depend on never crashes on an all-defaults config.
    """

    def test_defaults_resolve_to_sensible_values(self) -> None:
        cfg = LauncherConfig(app_name="My App").resolve()
        assert cfg.app_slug == "my-app"
        assert cfg.container_name == cfg.image_name == cfg.compose_project == "my-app"
        assert actions.resolve_port(cfg) == cfg.default_port == 8080
        assert cfg.compose_path.name == "docker-compose.prod.yml"
        # the pure helper layer must not crash on defaults
        assert gui.buttons_for_state("not_installed") == [("install", "install")]
        assert gui.advanced_ports_visible(cfg) is False
        assert actions._env_port_updates(cfg) == {cfg.env_port_key: 8080}

    def test_custom_values_propagate(self) -> None:
        cfg = LauncherConfig(app_name="X", container_name="cn", default_port=9090, env_port_key="CUSTOM_PORT").resolve()
        assert cfg.container_name == "cn"
        assert actions.resolve_port(cfg) == 9090
        assert actions._env_port_updates(cfg) == {"CUSTOM_PORT": 9090}
