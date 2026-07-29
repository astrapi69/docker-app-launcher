"""Coverage check for path-bearing config fields (#85).

The #83 defect was not a wrong base - it was a NEW path field
(``image_archive``) giving itself an inline base rule instead of using the
central one. An identity test between two fields catches that one case; it
does not catch the next field taking the same shortcut. This module turns
the single proof into a RULE:

- Every dataclass field whose name looks path-bearing MUST be classified in
  ``PATH_FIELD_ANCHORS``. A new field that matches the pattern and is not
  classified fails ``test_every_path_suggestive_field_is_classified`` - no
  silent slip-through (same logic as the gates' coverage checks).
- Every field classified ``base_dir`` is PROVEN to resolve through
  ``_base_dir()``: under an explicit ``install_dir``, anchored to the config
  file's directory when file-loaded (with the cwd moved elsewhere, so the
  test proves the base it claims), and flagged on the cwd fallback.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from docker_app_launcher.config import LauncherConfig

_PATH_SUGGESTIVE = re.compile(r"(_file|_dir|_path|_archive|_context|_paths)$")

# The classification is the CONTRACT: how each path-bearing field anchors.
# "base_dir"       - resolves through config._base_dir() (install_dir; from_json
#                    anchors it to the config file's own directory; cwd fallback
#                    is flagged via base_is_cwd_fallback). The only valid rule
#                    for consumer-app-relative paths (#64, #83).
# "build_context"  - relative to build_context_path, itself base_dir-anchored.
# "config_dir"     - joined onto config_path.
# "is_the_base"    - install_dir IS the base the rule anchors to.
# "home_default"   - defaults to an absolute path under the user home.
# "as_given"       - used verbatim (icons, OS app paths); consumers pass
#                    absolute paths.
# "expanduser"     - each entry is expanduser()'d and used as given.
# "url"            - a URL path on the app's HTTP server, not a filesystem path.
PATH_FIELD_ANCHORS: dict[str, str] = {
    "compose_file": "base_dir",
    "build_context": "base_dir",
    "image_archive": "base_dir",
    "dockerfile_file": "build_context",
    "install_dir": "is_the_base",
    "config_dir": "home_default",
    "manifest_file": "config_dir",
    "icon_path": "as_given",
    "tray_icon_path": "as_given",
    "docker_desktop_path": "as_given",
    "cleanup_search_paths": "expanduser",
    "health_check_path": "url",
    "browser_path": "url",
}

# (field, resolved-property, sample relative value) for every base_dir field.
_BASE_DIR_PROPERTIES = [
    ("compose_file", "compose_path", "dc.yml"),
    ("build_context", "build_context_path", "srcdir"),
    ("image_archive", "image_archive_path", "img.tar"),
]


def _path_suggestive_fields() -> list[str]:
    return [f.name for f in dataclasses.fields(LauncherConfig) if _PATH_SUGGESTIVE.search(f.name)]


class TestEnumeration:
    def test_every_path_suggestive_field_is_classified(self) -> None:
        unclassified = [name for name in _path_suggestive_fields() if name not in PATH_FIELD_ANCHORS]
        assert not unclassified, (
            f"new path-bearing config field(s) {unclassified} are not classified in "
            "PATH_FIELD_ANCHORS - decide how each anchors (a consumer-app-relative "
            "path MUST resolve via _base_dir(), never an inline rule; precedent #83) "
            "and add a base-proof test if it is base_dir-anchored"
        )

    def test_no_stale_classification(self) -> None:
        field_names = {f.name for f in dataclasses.fields(LauncherConfig)}
        stale = [name for name in PATH_FIELD_ANCHORS if name not in field_names]
        assert not stale, f"PATH_FIELD_ANCHORS classifies removed field(s): {stale}"

    def test_every_base_dir_field_has_a_proof_row(self) -> None:
        classified = {name for name, anchor in PATH_FIELD_ANCHORS.items() if anchor == "base_dir"}
        proven = {row[0] for row in _BASE_DIR_PROPERTIES}
        assert classified == proven, (
            f"base_dir-classified fields {sorted(classified)} and proof rows {sorted(proven)} "
            "must match - every base_dir field gets its resolution proven below"
        )


class TestBaseDirResolutionProof:
    @pytest.mark.parametrize(("field", "prop", "value"), _BASE_DIR_PROPERTIES)
    def test_explicit_install_dir_is_the_base(self, field: str, prop: str, value: str, tmp_path: Path) -> None:
        cfg = LauncherConfig(app_name="X", install_dir=str(tmp_path))
        setattr(cfg, field, value)
        cfg.resolve()
        assert getattr(cfg, prop) == tmp_path / value
        assert cfg.base_is_cwd_fallback is False

    @pytest.mark.parametrize(("field", "prop", "value"), _BASE_DIR_PROPERTIES)
    def test_file_loaded_config_anchors_to_the_config_dir(
        self, field: str, prop: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Prove the base: cwd is moved elsewhere, so only the config file's
        # directory can explain the result (the frozen-wrapper case, #64/#83).
        cfg_dir = tmp_path / "bundle"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "launcher.json"
        unsaved = LauncherConfig(app_name="X")
        setattr(unsaved, field, value)
        unsaved.to_json(cfg_file)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        loaded = LauncherConfig.from_json(cfg_file).resolve()
        assert getattr(loaded, prop) == cfg_dir / value
        assert loaded.base_is_cwd_fallback is False

    @pytest.mark.parametrize(("field", "prop", "value"), _BASE_DIR_PROPERTIES)
    def test_cwd_fallback_is_flagged_never_silent(
        self, field: str, prop: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = LauncherConfig(app_name="X")
        setattr(cfg, field, value)
        cfg.resolve()
        assert getattr(cfg, prop) == tmp_path / value
        assert cfg.base_is_cwd_fallback is True, "the fragile base must be FLAGGED for the gates"


class TestTransitiveAnchors:
    def test_dockerfile_file_anchors_to_the_build_context(self, tmp_path: Path) -> None:
        cfg = LauncherConfig(
            app_name="X", install_dir=str(tmp_path), build_context="ctx", dockerfile_file="Dockerfile.prod"
        ).resolve()
        assert cfg.dockerfile_path == tmp_path / "ctx" / "Dockerfile.prod"

    def test_manifest_file_anchors_to_the_config_dir(self, tmp_path: Path) -> None:
        cfg = LauncherConfig(app_name="X", config_dir=str(tmp_path / ".x"), manifest_file="m.json").resolve()
        assert cfg.manifest_path == tmp_path / ".x" / "m.json"
