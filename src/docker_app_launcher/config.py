"""Launcher configuration: a single dataclass that drives everything.

The whole launcher is configuration-driven. There is NO hard-coded app name,
container name, port, health endpoint or path anywhere in :mod:`actions`,
:mod:`gui` or :mod:`tray` - every one of those reads it from a
:class:`LauncherConfig` instance. That is what makes the same code base usable
for any Docker-based application.

The dataclass is pure data plus a handful of pure helpers
(:meth:`LauncherConfig.resolve` and the path/filter helpers), so it is fully
unit-testable without Docker, a display, or any third-party dependency.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Callbacks are excluded from (de)serialization; typed loosely on purpose so
# users can pass any plain callable.
Callback = Callable[..., Any]

# The locales shipped as ``i18n/<code>.yaml`` catalogs. ``locale`` may be set to
# any of these; an unknown locale falls back to English at lookup time. Kept in
# lock-step with the YAML files by ``test_i18n`` (``available_languages()``).
SUPPORTED_LOCALES = ["de", "en", "el", "es", "fr", "hi", "ja", "ko", "pt", "tr", "id"]

# Native-script display labels for the language picker (a language is shown in
# its own script - "Ελληνικά", not "Greek").
LOCALE_LABELS = {
    "de": "Deutsch",
    "en": "English",
    "el": "Ελληνικά",
    "es": "Español",
    "fr": "Français",
    "hi": "हिन्दी",
    "ja": "日本語",
    "ko": "한국어",
    "pt": "Português",
    "tr": "Türkçe",
    "id": "Bahasa Indonesia",
}


def locale_for_label(label: str) -> str | None:
    """Reverse-map a native label (``"Deutsch"``) to its code (``"de"``), or None."""
    for code, lbl in LOCALE_LABELS.items():
        if lbl == label:
            return code
    return None


def detect_system_locale() -> str:
    """Best-effort detection of the OS UI language as a supported code.

    Maps e.g. ``de_DE`` -> ``de``. Returns ``"en"`` when the system locale is
    unset, unreadable, or not one of :data:`SUPPORTED_LOCALES`.
    """
    import locale as _locale

    try:
        lang = _locale.getlocale()[0] or _locale.getdefaultlocale()[0]
    except Exception:  # noqa: BLE001 - locale APIs can raise on odd systems
        return "en"
    if lang:
        code = lang.replace("-", "_").split("_")[0].lower()
        if code in SUPPORTED_LOCALES:
            return code
    return "en"


@dataclass
class LauncherConfig:
    """Everything the launcher needs to manage one Docker application.

    Only :attr:`app_name` is normally required; :meth:`resolve` fills in the
    derived defaults (slug, container/image names, compose project, config
    directory, releases URL). Call :meth:`resolve` once after constructing the
    config and before handing it to the launcher (``launch()`` does this for
    you).
    """

    # === App identity ===
    app_name: str = "My App"
    app_slug: str = ""
    container_name: str = ""
    image_name: str = ""
    compose_project: str = ""

    # === Network / health ===
    default_port: int = 8080
    health_check_path: str = "/api/health"
    health_check_key: str = "status"
    health_check_value: str = "ok"
    health_check_timeout: int = 60
    browser_path: str = "/"
    env_port_key: str = "APP_PORT"

    # === Internal (container) ports - expert-only ===
    # ``internal_ports`` maps a logical name (e.g. "backend", "nginx") to its
    # default in-container port; ``env_internal_port_keys`` maps the same name
    # to the ``.env`` variable Compose substitutes. Unlike the public host port,
    # changing an internal port requires an image REBUILD. ``show_advanced_ports``
    # gates the launcher's collapsed expert section; with the maps empty (the
    # default) the feature is invisible and inert.
    internal_ports: dict[str, int] = field(default_factory=dict)
    env_internal_port_keys: dict[str, str] = field(default_factory=dict)
    show_advanced_ports: bool = False

    # === Docker ===
    compose_file: str = "docker-compose.prod.yml"
    build_timeout: int = 600
    start_timeout: int = 120
    stop_timeout: int = 30
    # Hint for the build progress bar: the number of build steps to expect. 0 =
    # auto-detect from the streamed ``docker build`` output (best-effort, the
    # percentage converges as the build proceeds); set it (e.g. 38) for a smooth
    # bar from the first step.
    estimated_build_steps: int = 0

    # === Paths ===
    icon_path: str = ""
    # Separate tray icon; falls back to ``icon_path`` when empty, and to a
    # generated initial-on-a-tile default when both are empty (never pystray's
    # bare default square).
    tray_icon_path: str = ""
    config_dir: str = ""
    install_dir: str = ""
    manifest_file: str = "install-manifest.json"

    # === GUI ===
    window_width: int = 620
    window_height: int = 470
    window_resizable: bool = False
    # ``"auto"`` detects the OS language (resolved by :meth:`resolve`); any
    # explicit code in :data:`SUPPORTED_LOCALES` overrides it.
    locale: str = "auto"

    # === Single instance ===
    single_instance: bool = True

    # === Logging ===
    log_level: str = "INFO"
    log_max_size: int = 5_000_000
    log_backup_count: int = 3

    # === Links ===
    repo_url: str = ""
    releases_url: str = ""
    docs_url: str = ""

    # === Docker check ===
    # Optional overrides for the platform-specific Docker diagnostics. Empty =
    # use the platform default (Docker's official install URL / Desktop path).
    docker_desktop_path: str = ""
    docker_install_url: str = ""

    # === Update check ===
    # ``app_version`` is the version this launcher ships for; the update
    # check compares it against the latest GitHub release of ``repo_url``.
    update_check_enabled: bool = True
    app_version: str = ""

    # === Cleanup ===
    cleanup_on_start: bool = True
    legacy_names: list[str] = field(default_factory=list)
    # Explicit config directories offered for removal when they still exist.
    cleanup_configs: list[str] = field(default_factory=list)
    # Base directories scanned for ``legacy_names`` subdirectories (e.g.
    # ``~/.config`` -> ``~/.config/<legacy-name>``, ``~`` -> ``~/.<legacy-name>``).
    # Lets cleanup find leftover config dirs without listing each one explicitly.
    cleanup_search_paths: list[str] = field(default_factory=list)

    # === Tray ===
    tray_enabled: bool = True
    tray_minimize_on_close: bool = True

    # === i18n ===
    custom_strings: dict[str, dict[str, str]] = field(default_factory=dict)

    # === Callbacks (never serialized) ===
    on_before_install: Callback | None = None
    on_after_install: Callback | None = None
    on_before_start: Callback | None = None
    on_after_start: Callback | None = None
    on_error: Callback | None = None

    # --- derivation -------------------------------------------------------

    def resolve(self) -> LauncherConfig:
        """Fill derived defaults from :attr:`app_name`. Idempotent.

        Returns ``self`` so it can be chained, e.g.
        ``LauncherConfig(app_name="X").resolve()``.
        """
        if not self.app_slug:
            self.app_slug = slugify(self.app_name)
        if not self.container_name:
            self.container_name = self.app_slug
        if not self.image_name:
            self.image_name = self.app_slug
        if not self.compose_project:
            self.compose_project = self.app_slug
        if not self.config_dir:
            self.config_dir = str(Path.home() / f".{self.app_slug}")
        if not self.releases_url and self.repo_url:
            self.releases_url = f"{self.repo_url.rstrip('/')}/releases/latest"
        if self.locale == "auto":
            self.locale = detect_system_locale()
        return self

    # --- computed paths / filters (pure) ----------------------------------

    @property
    def config_path(self) -> Path:
        """Directory that holds the launcher's persisted state."""
        return Path(self.config_dir).expanduser()

    @property
    def launcher_config_file(self) -> Path:
        """JSON file holding the user's persisted launcher settings (port...)."""
        return self.config_path / "launcher.json"

    @property
    def lock_path(self) -> Path:
        """Single-instance PID lockfile (under the config directory)."""
        return self.config_path / "launcher.lock"

    @property
    def log_path(self) -> Path:
        """Persistent launcher log (rotated)."""
        return self.config_path / "launcher.log"

    @property
    def install_log_path(self) -> Path:
        """Activity log of the most recent install/uninstall run."""
        return self.config_path / "install.log"

    @property
    def manifest_path(self) -> Path:
        """Path of the install manifest."""
        return self.config_path / self.manifest_file

    @property
    def compose_path(self) -> Path:
        """Absolute path of the compose file (relative to ``install_dir``)."""
        compose = Path(self.compose_file).expanduser()
        if compose.is_absolute():
            return compose
        base = Path(self.install_dir).expanduser() if self.install_dir else Path.cwd()
        return base / compose

    def name_filters(self) -> list[str]:
        """``docker --filter name=`` values: the container plus legacy names."""
        names = [self.container_name, *self.legacy_names]
        return [n for n in names if n]

    def image_patterns(self) -> list[str]:
        """Image-reference patterns: the image plus legacy names."""
        names = [self.image_name, *self.legacy_names]
        return [n for n in names if n]

    def cleanup_patterns(self) -> list[str]:
        """Name patterns the startup cleanup scans for (container + legacy)."""
        names = [self.container_name, self.image_name, *self.legacy_names]
        seen: set[str] = set()
        out: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    # --- (de)serialization ------------------------------------------------

    @classmethod
    def from_json(cls, path: str | Path) -> LauncherConfig:
        """Load a config from ``path`` (or an all-defaults config if absent).

        Unknown keys are ignored so a config file written by a newer version
        never crashes an older launcher. The result is always
        :meth:`resolve`-d.
        """
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            cfg = cls(**valid)
        else:
            cfg = cls()
        cfg.resolve()
        return cfg

    def to_json(self, path: str | Path) -> None:
        """Write the config to ``path`` as pretty JSON (callbacks excluded)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in dataclasses.asdict(self).items() if not callable(v) and v is not None}
        p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def slugify(text: str) -> str:
    """Turn an app name into a lowercase, hyphen-separated slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
