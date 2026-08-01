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

# What ``appearance`` may be set to (#118). Kept here rather than imported
# from ``appearance`` so the config module stays free of that import cycle;
# a test pins the two lists against each other.
APPEARANCE_CHOICES = ("system", "light", "dark")

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
    # Deployment mode (#51, #78): "compose" (default for existing configs -
    # a configured compose file keeps working unchanged), "dockerfile"
    # (single-service build/run directly through the docker-py API, zero
    # compose dependency) or "image" (run a PREBUILT image - pull via the
    # engine API or load a local archive; zero build toolchain on the user
    # machine, works on Docker generations without compose/buildx). ""
    # resolves to "compose". Any other value is a hard error at resolve()
    # time (#32 philosophy: never guess).
    deployment_mode: str = ""
    # --- dockerfile-mode block (ignored in compose mode) ---
    dockerfile_file: str = "Dockerfile"  # relative to build_context
    build_context: str = "."  # relative to install_dir
    # Container-internal port the published host port maps onto; 0 = same
    # as the resolved host port.
    container_port: int = 0
    # Host interface the published port binds to, for the modes the launcher
    # publishes itself (image, dockerfile). Default LOCALHOST (#111): the
    # measurement showed docker-py's bare-int port form publishes on 0.0.0.0
    # AND ::, i.e. every interface, while the docs promised localhost. Apps
    # without authentication are then reachable from the whole network.
    # Set "0.0.0.0" to open deliberately - the launcher warns visibly at that
    # moment. In compose mode the APP's compose file decides, so this field
    # does not apply there (--doctor reports what compose actually bound).
    bind_address: str = "127.0.0.1"
    # Named volumes: {volume_name: container_mount_path}.
    container_volumes: dict[str, str] = field(default_factory=dict)
    container_env: dict[str, str] = field(default_factory=dict)
    restart_policy: str = "unless-stopped"
    # Registry credential resolution for dockerfile-mode builds (#77).
    # Default False: the launcher builds from local Dockerfiles with PUBLIC
    # base images and must not touch the user's credential helpers - a stale
    # credsStore (e.g. leftover docker-credential-gcloud) would hard-fail
    # docker-py where the CLI shrugs. Consumers that pull PRIVATE images
    # declare it explicitly; only then is a broken helper a hard error.
    use_registry_credentials: bool = False
    # --- image-mode block (#78; ports/volumes/env/restart above are shared
    # with dockerfile mode) ---
    # Image to run: tag ("ghcr.io/owner/app:1.2.3") or digest pin
    # ("ghcr.io/owner/app@sha256:..." - immutability guarantee).
    image_reference: str = ""
    # Optional registry-free path: a local image archive (docker save
    # format), relative to install_dir. When configured AND present it is
    # loaded via the engine API INSTEAD of pulling; else the pull runs.
    image_archive: str = ""
    compose_file: str = "docker-compose.prod.yml"
    build_timeout: int = 600
    start_timeout: int = 120
    stop_timeout: int = 30
    # Free-space floor (bytes) checked on the build directory BEFORE a build,
    # so a multi-minute build does not fail deep in on ENOSPC (G4, #61). The
    # default (~2 GB) is advisory - a clearly-insufficient signal, not a precise
    # estimate. Set to 0 to disable the check.
    min_build_disk_bytes: int = 2_000_000_000
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
    window_height: int = 520
    # Resizable by default: the log panel is the window's core (P0) and a
    # fixed 620x520 clips it on small screens / large fonts. The persisted
    # geometry (#31) keeps whatever size the user settles on. Apps that
    # need a fixed layout opt out with window_resizable=False.
    window_resizable: bool = True
    # ``"auto"`` detects the OS language (resolved by :meth:`resolve`); any
    # explicit code in :data:`SUPPORTED_LOCALES` overrides it.
    locale: str = "auto"
    # Which frontend renders the window: ``"tk"`` (built-in) or any name
    # registered under the ``docker_app_launcher.frontends`` entry-point group.
    # All frontends share the same behaviour tables (:mod:`ui_model`).
    gui_backend: str = "tk"
    # Light, dark, or follow the system (#118). "system" asks the OS through
    # its own tools (XDG portal / defaults / reg), three-valued; where the
    # system says nothing the launcher renders light and LOGS that it did.
    appearance: str = "system"

    # === Single instance ===
    single_instance: bool = True

    # === Logging ===
    log_level: str = "INFO"
    log_max_size: int = 5_000_000
    log_backup_count: int = 3
    # How many container-log lines the "App logs" button fetches (P2).
    log_tail_lines: int = 200

    # === Links ===
    repo_url: str = ""
    releases_url: str = ""
    docs_url: str = ""

    # === Docker check ===
    # Optional overrides for the platform-specific Docker diagnostics. Empty =
    # use the platform default (Docker's official install URL / Desktop path).
    docker_desktop_path: str = ""
    docker_install_url: str = ""

    # === Docker minimum versions (app-declared capability floor) ===
    # What the APP's Dockerfile / compose file demands of the Docker
    # environment. All optional; empty = not declared, so ONLY the
    # launcher's intrinsic, non-negotiable requirements apply (compose mode
    # needs buildx >= 0.17 when compose >= 2.40.2). The config can only RAISE
    # the bar, never lower it: a value below the intrinsic threshold is
    # warned about in the log and the intrinsic value wins. Prefer
    # ``min_api_version`` over ``min_engine_version`` for engine-feature
    # checks - the Docker API version is the more robust signal across
    # distributions. Unparsable values are a hard error at resolve() (#32:
    # never guess, never silently ignore). Backward compatible: existing
    # configs declare nothing and keep their behaviour.
    min_engine_version: str = ""
    min_api_version: str = ""
    min_compose_version: str = ""
    min_buildx_version: str = ""

    # === Update check ===
    # ``app_version`` is the version this launcher ships for; the update
    # check compares it against the latest GitHub release of ``repo_url``.
    update_check_enabled: bool = True
    app_version: str = ""
    # JSON key in the health_check_path response that carries the RUNNING
    # app's version (#35). Empty disables the runtime probe; the About
    # surface then falls back to the install manifest / app_version.
    app_version_health_key: str = "version"

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
        if self.appearance not in APPEARANCE_CHOICES:
            raise ValueError(
                f"appearance must be one of {', '.join(APPEARANCE_CHOICES)}, got {self.appearance!r} "
                "(#118 - an unreadable value must fail here, not resolve to a silent default)"
            )
        if self.deployment_mode not in ("", "compose", "dockerfile", "image"):
            raise ValueError(
                f"deployment_mode must be 'compose', 'dockerfile' or 'image', got {self.deployment_mode!r} "
                "(#51/#78 - the mode is explicit, never guessed)"
            )
        if self.deployment_mode == "image" and not self.image_reference:
            # Even the archive path needs the reference: the loaded image is
            # started BY that name, so an unset reference can never work (#78).
            raise ValueError(
                "deployment_mode 'image' requires image_reference (tag or digest); "
                "an optional image_archive is loaded INTO that reference, not instead of it (#78)"
            )
        self._validate_min_versions()
        return self

    def _validate_min_versions(self) -> None:
        """Reject an unparsable declared Docker minimum at resolve() time.

        A version the launcher cannot understand must never be silently
        ignored (that would let a consumer THINK it raised the bar while the
        check does nothing). An empty string means "not declared" and is
        always fine (#32 philosophy: hard error, clear message).
        """
        for field_name in ("min_engine_version", "min_api_version", "min_compose_version", "min_buildx_version"):
            raw = getattr(self, field_name)
            if raw and normalize_version_core(raw) is None:
                raise ValueError(
                    f"{field_name}={raw!r} is not a parseable version (expected e.g. '0.17' or '2.40.2'); "
                    "a declared Docker minimum must be a real version, never guessed (#32)"
                )
        return

    @property
    def effective_deployment_mode(self) -> str:
        """``"compose"``, ``"dockerfile"`` or ``"image"`` - the default rule
        for existing configs is compose, so no consumer breaks (#51, #78)."""
        return self.deployment_mode or "compose"

    @property
    def build_context_path(self) -> Path:
        """Absolute build context for dockerfile mode (relative to install_dir)."""
        context = Path(self.build_context).expanduser()
        if context.is_absolute():
            return context
        return self._base_dir()[0] / context

    @property
    def image_archive_path(self) -> Path | None:
        """Absolute path of the optional image-mode archive, or None.

        A relative ``image_archive`` resolves against the SAME base as every
        other consumer path (compose file, build context): ``_base_dir()`` -
        an explicit ``install_dir``, which ``from_json`` anchors to the config
        file's own directory (#64, #83). One rule, so a frozen binary never
        silently resolves against its unpack directory; the cwd fallback is
        flagged and the readiness gate names it.
        """
        if not self.image_archive:
            return None
        archive = Path(self.image_archive).expanduser()
        if archive.is_absolute():
            return archive
        return self._base_dir()[0] / archive

    @property
    def dockerfile_path(self) -> Path:
        """Absolute Dockerfile path for dockerfile mode."""
        dockerfile = Path(self.dockerfile_file).expanduser()
        if dockerfile.is_absolute():
            return dockerfile
        return self.build_context_path / dockerfile

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

    def _base_dir(self) -> tuple[Path, bool]:
        """``(base, is_cwd_fallback)`` for app-relative paths (compose file,
        build context).

        Prefer an explicit ``install_dir``; else the current working directory
        - which is unreliable for a frozen binary launched from a ``.desktop``
        file, a Snap, or a file-manager double-click (G3, #64). ``from_json``
        fills ``install_dir`` from the config file's own directory, so a
        file-loaded config never silently depends on the CWD.
        """
        if self.install_dir:
            return Path(self.install_dir).expanduser(), False
        return Path.cwd(), True

    @property
    def base_is_cwd_fallback(self) -> bool:
        """Whether app-relative paths resolve against the fragile CWD (G3, #64).

        When True and a build fails to find the compose file / Dockerfile, the
        readiness gate says so loudly and advises setting ``install_dir``,
        rather than silently building against the wrong directory.
        """
        return self._base_dir()[1]

    @property
    def compose_path(self) -> Path:
        """Absolute path of the compose file (relative to ``install_dir``)."""
        compose = Path(self.compose_file).expanduser()
        if compose.is_absolute():
            return compose
        return self._base_dir()[0] / compose

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
    def from_json(cls, path: str | Path, *, require: bool = False) -> LauncherConfig:
        """Load a config from ``path`` (or an all-defaults config if absent).

        Unknown keys are ignored so a config file written by a newer version
        never crashes an older launcher. The result is always
        :meth:`resolve`-d.

        ``require=True`` raises :class:`FileNotFoundError` when the file is
        missing (#32): an EXPLICITLY passed ``--config`` path that does not
        exist is a deployment bug, and silently launching an all-defaults
        "My App" window masked exactly that for several wrapper releases.
        The implicit ``launcher.json`` lookup stays fail-open.
        """
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            cfg = cls(**valid)
            # A file-loaded config that does not set install_dir gets the
            # config file's OWN directory as the base for app-relative paths
            # (compose file, build context), instead of the fragile current
            # working directory (G3, #64). The config file sits next to the
            # app it describes, so its directory is the robust default.
            if not cfg.install_dir:
                cfg.install_dir = str(p.resolve().parent)
            else:
                install = Path(cfg.install_dir).expanduser()
                if not install.is_absolute():
                    # A RELATIVE install_dir in a file-loaded config is
                    # relative to the config file, not to the accidental
                    # CWD - same rationale as the base rule above (#64).
                    # This is what makes checked-in example configs
                    # (test-configs/) portable across checkouts.
                    cfg.install_dir = str((p.resolve().parent / install).resolve())
        elif require:
            raise FileNotFoundError(f"config file not found: {p} (explicitly passed via --config)")
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


# A version's comparable core is the first ``major.minor[.patch...]`` run.
_VERSION_CORE_RE = re.compile(r"\d+(?:\.\d+)+")


def normalize_version_core(raw: str) -> str | None:
    """Extract the comparable dotted-numeric core from a dirty version string.

    Real Docker version strings are messy: ``v0.8.2-docker`` (buildx),
    ``20.10.21+dfsg1`` (Debian engine), ``Docker Compose version v2.40.2``,
    ``github.com/docker/buildx v0.17.1 <sha>``. This pulls out the first
    ``major.minor[.patch...]`` run so a real version library can compare it
    - stripping the leading ``v``, any ``+build`` / ``-suffix`` and any
    surrounding prose. Returns ``None`` when the string carries no version at
    all (``"latest"``, ``""``); the caller treats that as unparsable.
    """
    match = _VERSION_CORE_RE.search(raw or "")
    return match.group(0) if match else None
