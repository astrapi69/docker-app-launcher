"""Tests for LauncherConfig: derivation, paths, filters, (de)serialization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docker_app_launcher import actions, gui
from docker_app_launcher.config import (
    LOCALE_LABELS,
    SUPPORTED_LOCALES,
    LauncherConfig,
    detect_system_locale,
    locale_for_label,
    slugify,
)


class TestLocale:
    def test_detect_maps_de_de_to_de(self, monkeypatch) -> None:
        import locale

        monkeypatch.setattr(locale, "getlocale", lambda *a: ("de_DE", "UTF-8"))
        assert detect_system_locale() == "de"

    def test_detect_unknown_falls_back_to_en(self, monkeypatch) -> None:
        import locale

        monkeypatch.setattr(locale, "getlocale", lambda *a: ("xx_XX", None))
        monkeypatch.setattr(locale, "getdefaultlocale", lambda *a: (None, None))
        assert detect_system_locale() == "en"

    def test_resolve_auto_uses_detection(self, monkeypatch) -> None:
        # The autouse fixture pins detection to "en".
        cfg = LauncherConfig(app_name="X", locale="auto").resolve()
        assert cfg.locale == "en"

    def test_resolve_explicit_locale_preserved(self) -> None:
        assert LauncherConfig(app_name="X", locale="fr").resolve().locale == "fr"

    def test_labels_cover_all_supported(self) -> None:
        assert set(LOCALE_LABELS) == set(SUPPORTED_LOCALES)
        assert LOCALE_LABELS["el"] == "Ελληνικά"  # native script, not "Greek"

    def test_locale_for_label_round_trip(self) -> None:
        assert locale_for_label("Deutsch") == "de"
        assert locale_for_label("日本語") == "ja"
        assert locale_for_label("Not a language") is None


class TestNewConfigDefaults:
    def test_defaults(self) -> None:
        cfg = LauncherConfig(app_name="X")
        assert cfg.locale == "auto"
        assert cfg.single_instance is True
        assert cfg.log_level == "INFO"
        assert cfg.log_max_size == 5_000_000
        assert cfg.log_backup_count == 3
        assert cfg.estimated_build_steps == 0


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

    def test_cleanup_search_paths_default_and_round_trip(self, tmp_path: Path) -> None:
        assert LauncherConfig(app_name="X").resolve().cleanup_search_paths == []
        path = tmp_path / "cfg.json"
        LauncherConfig(app_name="X", cleanup_search_paths=["~/.config", "~"]).resolve().to_json(path)
        assert LauncherConfig.from_json(path).cleanup_search_paths == ["~/.config", "~"]

    def test_tray_icon_path_default_and_round_trip(self, tmp_path: Path) -> None:
        assert LauncherConfig(app_name="X").resolve().tray_icon_path == ""
        path = tmp_path / "cfg.json"
        LauncherConfig(app_name="X", tray_icon_path="t.png").resolve().to_json(path)
        assert LauncherConfig.from_json(path).tray_icon_path == "t.png"

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
        assert gui.button_enabled("not_installed", "install") is True
        assert gui.advanced_ports_visible(cfg) is False
        assert actions._env_port_updates(cfg) == {cfg.env_port_key: 8080}

    def test_custom_values_propagate(self) -> None:
        cfg = LauncherConfig(app_name="X", container_name="cn", default_port=9090, env_port_key="CUSTOM_PORT").resolve()
        assert cfg.container_name == "cn"
        assert actions.resolve_port(cfg) == 9090
        assert actions._env_port_updates(cfg) == {"CUSTOM_PORT": 9090}


class TestDetectSystemLocaleEdges:
    def test_locale_api_raises_falls_back_to_en(self, monkeypatch) -> None:
        import locale as _locale

        def boom():
            raise ValueError("unknown locale")

        monkeypatch.setattr(_locale, "getlocale", boom)
        assert detect_system_locale() == "en"

    def test_unsupported_language_falls_back_to_en(self, monkeypatch) -> None:
        import locale as _locale

        monkeypatch.setattr(_locale, "getlocale", lambda: ("xx_XX", "UTF-8"))
        assert detect_system_locale() == "en"

    def test_dash_separated_tag_normalized(self, monkeypatch) -> None:
        import locale as _locale

        monkeypatch.setattr(_locale, "getlocale", lambda: ("de-DE", "UTF-8"))
        assert detect_system_locale() == "de"


class TestWindowResizable:
    def test_resizable_by_default(self) -> None:
        # The log panel is the window's core; a fixed size clips it on small
        # screens, so resizing must work out of the box.
        assert LauncherConfig(app_name="X").window_resizable is True

    def test_opt_out_survives_json_roundtrip(self, tmp_path) -> None:
        import json

        path = tmp_path / "launcher.json"
        path.write_text(json.dumps({"app_name": "X", "window_resizable": False}), encoding="utf-8")
        assert LauncherConfig.from_json(path).window_resizable is False


class TestFromJsonRequire:
    """#32: require=True turns a missing file into a loud FileNotFoundError."""

    def test_missing_with_require_raises(self, tmp_path) -> None:
        import pytest

        with pytest.raises(FileNotFoundError, match="explicitly passed"):
            LauncherConfig.from_json(tmp_path / "gone.json", require=True)

    def test_missing_without_require_stays_fail_open(self, tmp_path) -> None:
        cfg = LauncherConfig.from_json(tmp_path / "gone.json")
        assert cfg.app_name  # all-defaults config, resolved

    def test_existing_file_ignores_require(self, tmp_path) -> None:
        path = tmp_path / "launcher.json"
        path.write_text('{"app_name": "Real App"}', encoding="utf-8")
        assert LauncherConfig.from_json(path, require=True).app_name == "Real App"


class TestMinVersionValidation:
    """#54: a declared Docker minimum must parse, or resolve() is a hard error."""

    def test_empty_means_not_declared(self) -> None:
        cfg = LauncherConfig(app_name="X").resolve()
        assert cfg.min_buildx_version == "" and cfg.min_engine_version == ""

    def test_valid_versions_accepted(self) -> None:
        cfg = LauncherConfig(
            app_name="X",
            min_engine_version="20.10",
            min_api_version="1.41",
            min_compose_version="2.40.2",
            min_buildx_version="0.17",
        ).resolve()
        assert cfg.min_compose_version == "2.40.2"

    def test_unparsable_min_version_is_a_hard_error(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="min_buildx_version"):
            LauncherConfig(app_name="X", min_buildx_version="latest").resolve()

    def test_dirty_but_real_version_string_is_accepted(self) -> None:
        # A consumer may paste a raw 'docker version' string; the core parses.
        cfg = LauncherConfig(app_name="X", min_engine_version="20.10.21+dfsg1").resolve()
        assert cfg.min_engine_version == "20.10.21+dfsg1"


class TestBuildBaseResolution:
    """G3 (#64): app-relative paths resolve robustly, never silently via CWD."""

    def test_explicit_install_dir_is_the_base(self, tmp_path: Path) -> None:
        cfg = LauncherConfig(app_name="X", install_dir=str(tmp_path), compose_file="dc.yml").resolve()
        assert cfg.base_is_cwd_fallback is False
        assert cfg.compose_path == tmp_path / "dc.yml"

    def test_unset_install_dir_is_the_cwd_fallback(self) -> None:
        cfg = LauncherConfig(app_name="X").resolve()  # programmatic config, no install_dir
        assert cfg.base_is_cwd_fallback is True

    def test_from_json_derives_install_dir_from_config_dir(self, tmp_path: Path) -> None:
        appdir = tmp_path / "app"
        appdir.mkdir()
        (appdir / "launcher.json").write_text('{"app_name": "X", "compose_file": "dc.yml"}', encoding="utf-8")
        cfg = LauncherConfig.from_json(appdir / "launcher.json")
        assert cfg.install_dir == str(appdir.resolve())
        assert cfg.base_is_cwd_fallback is False
        assert cfg.compose_path == appdir.resolve() / "dc.yml"

    def test_from_json_keeps_explicit_install_dir(self, tmp_path: Path) -> None:
        (tmp_path / "launcher.json").write_text('{"app_name": "X", "install_dir": "/opt/app"}', encoding="utf-8")
        cfg = LauncherConfig.from_json(tmp_path / "launcher.json")
        assert cfg.install_dir == "/opt/app"


class TestRelativeInstallDir:
    """A relative install_dir in a FILE-loaded config resolves against the
    config file's directory (#64 rationale) - this keeps checked-in example
    configs (test-configs/) portable across checkouts."""

    def test_relative_resolves_against_the_config_file(self, tmp_path) -> None:
        import json

        app_dir = tmp_path / "apps" / "solo"
        app_dir.mkdir(parents=True)
        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        path = cfg_dir / "launcher.json"
        path.write_text(json.dumps({"app_name": "X", "install_dir": "../apps/solo"}), encoding="utf-8")
        cfg = LauncherConfig.from_json(path)
        assert cfg.install_dir == str(app_dir.resolve())

    def test_absolute_install_dir_is_untouched(self, tmp_path) -> None:
        import json

        path = tmp_path / "launcher.json"
        path.write_text(json.dumps({"app_name": "X", "install_dir": str(tmp_path / "abs")}), encoding="utf-8")
        assert LauncherConfig.from_json(path).install_dir == str(tmp_path / "abs")

    def test_empty_install_dir_still_defaults_to_config_dir(self, tmp_path) -> None:
        import json

        path = tmp_path / "launcher.json"
        path.write_text(json.dumps({"app_name": "X"}), encoding="utf-8")
        assert LauncherConfig.from_json(path).install_dir == str(tmp_path.resolve())

    def test_kwargs_config_is_unaffected(self) -> None:
        # No config file, no file-relative rule: a programmatic relative
        # install_dir keeps its meaning (resolved against the CWD at use).
        assert LauncherConfig(app_name="X", install_dir="rel/path").resolve().install_dir == "rel/path"


class TestImageModeConfig:
    """Schema contract of the image deployment mode (#78)."""

    def test_image_mode_is_accepted(self, tmp_path) -> None:
        cfg = LauncherConfig(
            app_name="P",
            deployment_mode="image",
            image_reference="ghcr.io/o/a:1",
            install_dir=str(tmp_path),
        ).resolve()
        assert cfg.effective_deployment_mode == "image"

    def test_image_mode_without_image_reference_is_a_hard_error(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="image_reference"):
            LauncherConfig(app_name="P", deployment_mode="image", install_dir=str(tmp_path)).resolve()

    def test_archive_path_relative_to_install_dir(self, tmp_path) -> None:
        cfg = LauncherConfig(
            app_name="P",
            deployment_mode="image",
            image_reference="ghcr.io/o/a:1",
            image_archive="images/app.tar",
            install_dir=str(tmp_path),
        ).resolve()
        assert cfg.image_archive_path == tmp_path / "images" / "app.tar"

    def test_archive_path_absolute_wins(self, tmp_path) -> None:
        cfg = LauncherConfig(
            app_name="P",
            deployment_mode="image",
            image_reference="ghcr.io/o/a:1",
            image_archive=str(tmp_path / "abs.tar"),
            install_dir=str(tmp_path / "elsewhere"),
        ).resolve()
        assert cfg.image_archive_path == tmp_path / "abs.tar"

    def test_no_archive_means_none(self, tmp_path) -> None:
        cfg = LauncherConfig(
            app_name="P", deployment_mode="image", image_reference="ghcr.io/o/a:1", install_dir=str(tmp_path)
        ).resolve()
        assert cfg.image_archive_path is None

    def test_existing_modes_unaffected(self, tmp_path) -> None:
        # Backward compatibility: configs without the new fields resolve as before.
        cfg = LauncherConfig(app_name="P", install_dir=str(tmp_path)).resolve()
        assert cfg.effective_deployment_mode == "compose"
        assert cfg.image_reference == "" and cfg.image_archive_path is None


class TestImageArchiveResolutionBase:
    """#83: a relative image_archive uses the SAME base rule as every other
    consumer path (_base_dir), never silently the process cwd of a frozen
    binary. Each test proves it established the base it claims."""

    def test_file_loaded_config_anchors_to_the_config_dir(self, tmp_path, monkeypatch) -> None:
        # The frozen-wrapper case: config ships next to its assets; the
        # process cwd is somewhere else entirely (proven via chdir).
        cfg_dir = tmp_path / "bundle"
        (cfg_dir / "appdir").mkdir(parents=True)
        cfg_file = cfg_dir / "launcher.json"
        LauncherConfig(
            app_name="P",
            deployment_mode="image",
            image_reference="ghcr.io/o/a:1",
            install_dir="appdir",
            image_archive="img.tar",
        ).to_json(cfg_file)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        loaded = LauncherConfig.from_json(cfg_file).resolve()
        assert loaded.image_archive_path == cfg_dir / "appdir" / "img.tar"
        assert loaded.base_is_cwd_fallback is False

    def test_no_install_dir_falls_back_to_cwd_and_flags_it(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = LauncherConfig(
            app_name="P", deployment_mode="image", image_reference="ghcr.io/o/a:1", image_archive="img.tar"
        ).resolve()
        assert cfg.image_archive_path == tmp_path / "img.tar"
        assert cfg.base_is_cwd_fallback is True, "the fragile base must be FLAGGED, never silent"

    def test_same_base_as_compose_path(self, tmp_path) -> None:
        # One rule, not two: archive and compose file anchor identically.
        cfg = LauncherConfig(
            app_name="P",
            deployment_mode="image",
            image_reference="ghcr.io/o/a:1",
            image_archive="img.tar",
            install_dir=str(tmp_path),
        ).resolve()
        archive = cfg.image_archive_path
        assert archive is not None
        assert archive.parent == cfg.compose_path.parent == tmp_path
