# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Swappable GUI frontends.** The framework-neutral UI behaviour (button
  tables, per-state enablement, tooltip reasons, action dispatch, close
  policy) moved from `gui` into the new `ui_model` module — `gui` re-exports
  everything, so the existing API is unchanged. A new `frontends` registry
  resolves the window implementation by name: the new
  `LauncherConfig.gui_backend` field (default `"tk"`, also in
  `launcher.example.json`) selects it, and third-party packages can register
  alternatives (Qt, web, TUI, …) via the `docker_app_launcher.frontends`
  entry-point group — any module exposing `run(config, *, debug=False) -> int`
  qualifies. Every frontend renders the same `ui_model` tables, so behaviour
  cannot drift between toolkits.

- **Gated tag-publishing.** The tag-triggered PyPI workflow now refuses to
  publish unless the tag matches the `pyproject.toml` version, `CHANGELOG.md`
  has a section for it, and the full check chain (lint + format + types +
  tests) is green — a tag push can no longer ship unverified code.
- **72 new tests (326 -> 398), coverage 68 % -> 76 %.** The previously
  untested layers are now pinned: the docker-artifact enumeration helpers
  behind cleanup/uninstall (`_project_containers`, `_project_images`,
  `_docker_op`, `_docker_names`, `_image_size_bytes`, `_remove_config_path`),
  the streaming build runner `_stream_command` (tail, keep-limit, watchdog
  timeout, broken-callback safety), the `TrayController` runtime against a
  fake backend (start/stop, unreliable-backend refusal, setup timeout, crash
  fallback), Windows PID liveness via mocked `tasklist`, Docker-Desktop
  launch paths for Windows/macOS, the `launch()` convenience API, CLI
  `--start`, and the locale/update-check/logging/build-info error branches.
  `lockfile`, `logging_setup`, `update_check`, `build_info`, `pyinstaller`
  and `__init__` are at 100 % coverage.
- **Real-window GUI tests + automatic screenshots (445 tests, coverage
  86 %).** `tests/test_gui_window.py` drives a real `LauncherApp` window
  through Tk's own event layer (`invoke()`, synchronous worker threads) with
  all actions mocked: construction, per-state button enablement, the
  no-docker help panel, live language switching, log + clipboard, port
  validation, the threaded action flow (busy-guard, error hook), the cleanup
  offer, the progress bar, and background/close behaviour — one window per
  supported language. gui.py coverage 32 % -> 76 %. `pyautogui` (new dev
  dependency) captures best-effort PNGs of every state when
  `DAL_SCREENSHOTS=1` (`make screenshots`, dir `test-screenshots/`); CI runs
  the whole suite under `xvfb-run` and uploads the screenshots as a build
  artifact. New make targets `test-gui` and `screenshots`.

## [0.13.0] - 2026-07-23

### Added

- **Docker detection falls back to other contexts (#25).** When the ACTIVE
  docker context is unreachable (Docker Desktop for Linux / rootless setups
  where the active context points at a dead socket while the daemon runs under
  another one), detection now sweeps the remaining contexts with
  `DOCKER_HOST`-scoped `docker info` probes and, on a hit, **connects**: the
  working endpoint becomes a module-wide `DOCKER_HOST` override injected into
  every subsequent docker command, and the status says which context the
  launcher connected through. New public accessor
  `actions.docker_host_override()` returns that endpoint (or `None`).
  Permission-denied and timeout keep their dedicated messages and never
  trigger the sweep; CLIs without context support degrade to the old
  behaviour. stdlib-only as before.
- **Guarded publish targets.** `make publish` / `make publish-test` now
  refuse to upload a version that already exists on (Test)PyPI and ask for an
  explicit `y/N` confirmation before any upload — no more accidental releases
  once the check chain happens to pass.

### Fixed

- **"Docker is not started." finally says what was probed.** On total
  detection failure the new `docker_not_running_detail` message (all 11
  languages) names the checked context, its endpoint, and docker's own first
  stderr line. Both the CLI and the in-window Docker-help panel surface it.
- **Stale `dist/` artifacts poisoned builds and uploads.** `make build` now
  cleans `dist/` first; previously old wheels broke the `build-check` wheel
  inspection and would have been re-uploaded by `poetry publish`. The wheel
  listing also loops per wheel instead of assuming a single file.
- **codespell no longer flags the German architecture doc.**
  `src/docker_app_launcher/docs` joined the skip list (same reasoning as
  `README-de.md` and the i18n catalogs), unblocking `make release-check`.
- **`__version__` could report a stale version in dev environments.** It is
  read from the installed package metadata, which only updates on reinstall;
  the `bump-*` targets now run `poetry install --only-root` right after
  `poetry version` so the venv can no longer drift (it sat at 0.5.0).

## [0.12.1] - 2026-06-25

### Fixed

- **Clipped background-button label.** Shortened `run_in_background` to a concise
  "in background" noun phrase in all 11 languages (e.g. `Im Hintergrund
  weiterlaufen` -> `Im Hintergrund`); the long label was clipped at the button
  width in several locales.
- **Unbalanced primary grid.** The lone Copy-log button now sits in the right
  column (under Apply port) instead of dangling alone on the left.

## [0.12.0] - 2026-06-25

### Changed

- **Window relayout + button state pattern.** Every button is now ALWAYS visible
  and only enabled/disabled per state (never hidden/removed), with a tooltip on a
  disabled button explaining why. The primary actions sit in a fixed two-column
  grid above the log (`[Install] [Open browser]` / `[Start] [Stop]` /
  `[Uninstall] [Apply port]` / `[Copy log]`); the log area below is text +
  scrollbar only (the copy-log button moved up into the grid); a separator
  divides the log from the secondary row `[Cleanup] [Run in background]` at the
  bottom. Per-state enablement is a single `BUTTON_STATES` table; the `no_docker`
  state greys everything and shows the Docker-help panel. New `tooltip_*` reason
  strings in all 11 languages. Default window height 470 -> 520 for the taller
  grid.

### Added

- **Architecture documentation** (`docs/ARCHITECTURE.md`, German) covering the
  full module layout, state machine, and design decisions; plus `make`
  targets and `test-configs/` for manual launcher testing against real apps.

## [0.11.0] - 2026-06-25

### Changed

- **"Cleanup" button now available in every Docker-available state.** Previously
  only the running/stopped states carried the manual cleanup button; it now also
  appears in `not_installed`, because stale volumes, images, and configs can
  linger even before an install - not_installed: `[Install]` / `[Cleanup]`. The
  `no_docker` state is intentionally excluded (its screen is the "start Docker"
  help, and a Docker-backed cleanup scan cannot run without the daemon).

## [0.10.0] - 2026-06-24

### Added

- **Always-available "Cleanup" button.** The installed states now carry a manual
  cleanup button on the secondary row - running: `[Open] [Stop] [Uninstall]` /
  `[Apply port] [Run in background] [Cleanup]`; stopped: `[Start] [Uninstall]` /
  `[Cleanup]`. It is fully **decoupled from the startup cleanup offer** (which
  only fires once at launch when leftover artifacts already exist), so cleanup is
  reachable at any time. Clicking it scans on demand (`find_stale_artifacts`):
  if artifacts are found it shows the same selection offer; if not it reports
  "No leftover installation files found." New `cleanup_scanning` / `cleanup_none`
  strings in all 11 languages.

## [0.9.0] - 2026-06-24

### Added

- **"Copy log" button.** A small button above the scrollable log copies the
  entire log contents to the clipboard in one click - via Tk's built-in
  clipboard (`clipboard_clear` + `clipboard_append`), no extra dependency - so
  a user hitting an error can paste the full log straight into a bug report,
  email, or chat. The label flips to a localized "Copied!" for ~2s as feedback,
  then restores; an empty log is a safe no-op. New `log_copy` / `log_copied`
  strings in all 11 languages, relabeled live on a language switch.

## [0.8.0] - 2026-06-24

### Added

- **Platform-specific Docker diagnostics + guided start.** When Docker is down,
  the window now explains *why* per OS and offers the right next action:
  `check_docker_detailed()` distinguishes not-installed / daemon-stopped /
  no-permission (Linux group) / not-in-PATH (Desktop) / no-response, with a
  copy-pasteable command hint. A **Start Docker** button runs
  `systemctl start docker` (via `pkexec`) on Linux or launches Docker Desktop on
  Windows/macOS, and an **Open installation guide** button opens the right URL.
  New `docker_desktop_path` / `docker_install_url` config overrides. Every probe
  is guarded - it never raises.
- **Real-time progress bar.** A `ttk.Progressbar` above the log shows install /
  start / cleanup progress (determinate) and animates (indeterminate) during the
  health-check wait. Build progress is **parsed from the Docker build output**
  (`#<n> [stage x/y]` lines) rather than hard-coded - `DockerBuildProgress`
  tracks the highest step, or uses the new `estimated_build_steps` config hint
  for a smooth bar from the first line. Actions gained an `on_progress(percent,
  label)` callback (`percent=None` = indeterminate).

### Fixed

- **Cleanup never offers the active project's data volume (re-fix).** The
  previous guard only applied when containers were detected at scan time; the
  startup cleanup runs before that, so `<compose_project>_*` volumes (e.g.
  `adaptive-learner_adaptive-learner-data`) could still be listed. They are now
  excluded **unconditionally** - never offered, never deleted (deleting one
  while its container runs also blocks `docker volume rm`). Legacy volumes
  (different prefix) are still offered. New `cleanup_search_paths`-style debug
  log notes each protected volume.
- **No more silent gap during cleanup.** Every cleanup step now logs a line,
  including SKIPPED volumes - `Volume 'x' skipped (not selected)` and
  `Volume 'y' skipped (active project)` - so a run with no volume removals no
  longer looks frozen.

### Changed

- Default log rotation is now 5 MB × 3 backups (was 1 MB × 2), matching the
  documented defaults and `launcher.example.json`.

## [0.7.0] - 2026-06-24

### Added

- **In-window language picker + system-locale auto-detect.** The window shows a
  language dropdown (each language in its own script - "Ελληνικά", not "Greek")
  that switches the UI **live** and persists the choice to the launcher JSON.
  `locale` now defaults to `"auto"`, which `resolve()` maps to the OS language
  (`detect_system_locale()`), falling back to English; any explicit code
  overrides it. New `LOCALE_LABELS`, `locale_for_label()`, and
  `actions.resolve_locale()` / `set_locale()`.
- **Configurable single-instance + logging.** New `single_instance` (set
  `false` to allow multiple windows / skip the lockfile) and `log_level` /
  `log_max_size` / `log_backup_count` (previously hard-coded) `LauncherConfig`
  fields, all surfaced in a complete `launcher.example.json`.

### Changed

- `launcher.example.json` now documents every configurable field.

## [0.6.0] - 2026-06-24

### Added

- **11 UI languages.** The i18n catalog ships `de`, `en`, `el`, `es`, `fr`,
  `hi`, `ja`, `ko`, `pt`, `tr`, `id` as `i18n/<code>.yaml`. `config.locale`
  accepts any of them (`SUPPORTED_LOCALES`); an unknown locale falls back to
  English. Parity + placeholder-integrity tests cover every locale. (The 9 new
  languages are AI-translated and would benefit from native review.)
- **`cleanup_search_paths`** config field — base directories scanned for
  `legacy_names` subdirectories (`<base>/<name>` and `<base>/.<name>`), so
  cleanup finds leftover config dirs without listing each one explicitly.
- **README docs** (EN + DE) for custom icons, cleanup configuration,
  configuration paths, and the install manifest.

### Fixed

- **Cleanup no longer offers the active install's own data volume (#11).** A
  running install's Compose volume (`<compose_project>_*`, e.g.
  `myapp_myapp-data`) was listed as a stale artifact and offered for deletion -
  live user data. While the install is live (its containers still exist), its
  own project volumes are now excluded from the stale results regardless of the
  manifest; after uninstall the volume is reclaimable and shows up again. Legacy
  volumes (e.g. an old `bibliogon_*`) are unaffected.
- **German UI strings use real UTF-8 umlauts.** The DE catalog carried ASCII
  transliterations (`laeuft`, `oeffnen`, `fuer`, `Aenderung`, `weisst`, ...);
  they are now `läuft`, `öffnen`, `für`, `Änderung`, `weißt`, etc. A test guards
  against transliterations regressing.

### Changed

- **i18n moved from a Python dict to per-language YAML files.** Strings now live
  in `i18n/de.yaml` + `i18n/en.yaml` (flat keys, loaded once at startup);
  **adding a language is dropping a `<code>.yaml` file** beside them. The public
  API is unchanged - `t("key", config, **kwargs)`, `STRINGS`,
  `available_languages()` - so every call site and test is untouched. Adds a
  single runtime dependency, `pyyaml>=6.0`.
- **Two-row button layout in the running window.** The primary row keeps
  Open / Stop / Uninstall; "Apply port" and "Run in the background" move to a
  second row, so the fixed-width window no longer clips a 5th button.

## [0.5.0] - 2026-06-24

### Added

- **Explicit "Run in background" button + reliable Ubuntu tray (#9).** The
  running window now shows a visible **Run in the background** button instead of
  relying on the X + an often-broken tray. It (and the X) route through
  `tray.try_minimize_to_background`: when the system tray docks, the window is
  hidden to it; when it does not (no AppIndicator on Ubuntu/Wayland), the window
  is **minimized to the taskbar** instead, with a status hint - never silently
  killed. The X button now keeps a running app alive (tray, else taskbar) and
  only closes the launcher when the app is stopped (or the app opted out via
  `tray_minimize_on_close`). A separate **`tray_icon_path`** config field sets
  the tray icon (falling back to `icon_path`); when neither is set the tray
  shows a **generated default** - the app's initial on a colored tile, not
  pystray's bare square. pystray's **AppIndicator backend is now forced**
  (`pystray._appindicator`) rather than letting it auto-select the legacy X11
  backend that fires its setup callback but never docks; `PyGObject` is added to
  the `tray` extra (Linux-only marker). `--debug` logs tray diagnostics
  (import, backend, icon) so a missing tray needs no user debugging.

### Changed

- **Confirmed fully configuration-driven; dropped the last app-specific
  reference (#6).** An audit found the package already config-driven (the
  "extraction" was completed when the launcher moved into this package); the
  only app-specific string left was the `adaptive-learner` *example* in
  `pyinstaller.render_spec`'s docstring, now genericized to `my-app`. Added a
  minimal-config smoke test that pins the "runs from only `app_name`, all
  defaults sensible, helper layer never crashes" property.

## [0.4.0] - 2026-06-24

### Added

- **Configurable internal (container) ports for experts (#5).** New
  `LauncherConfig` fields `internal_ports` (logical name -> default container
  port), `env_internal_port_keys` (name -> `.env` variable Compose substitutes),
  and `show_advanced_ports`. The `.env` now carries ALL ports (public + every
  internal key); `set_internal_port` / `resolve_internal_port` persist + resolve
  them (internal ports allow the full 1-65535 range - e.g. nginx `:80` - since
  they are not host-published). New `change_internal_port()` action: unlike the
  public host port's seconds-fast no-rebuild recreate, an internal-port change
  **rebuilds** the images (Stop -> `.env` -> `up --build -d` -> health-check),
  with an `internal_port_rebuilding` progress line. The persistent window grows
  a collapsed **"Advanced settings (experts)"** section (gated by
  `show_advanced_ports`, hidden + inert by default) with a field + Apply button
  per internal port (Apply confirms the 2-5 min rebuild first), a warning, and a
  "Restore defaults" button. With the maps empty (the default) nothing changes:
  no `.env` keys, no UI, no behaviour shift.

## [0.3.0] - 2026-06-24

### Fixed

- **A port change now actually reaches Docker Compose (#3).** `set_port`
  persisted the new port to the launcher JSON and tried to mirror it into
  `.env`, but `_env_path` returned `None` whenever `install_dir` was empty, so
  the `.env` write was a silent no-op. The launcher then resolved the new port
  from its own JSON (for the health check + browser open) while Compose kept
  reading the old `.env` and republished the old port - so the app was
  unreachable on the port the launcher opened. `.env` is now written next to the
  compose file (`compose_path.parent`, which is `install_dir` when set and the
  CWD otherwise - exactly where Compose reads it), so the launcher and Compose
  can no longer disagree.

### Added

- **`actions.change_port()` - a verified, in-place host-port change.** Validate
  -> persist (launcher JSON + `.env`) -> if the stack is running, Stop and
  recreate with `up -d` (deliberately NOT `--build`: only the published host
  port changed, so the restart is seconds, not the minutes a rebuild costs) ->
  health-check on the **new** port. The persistent window now keeps the port
  field editable while running and adds an "Apply port" button that routes to
  it, with a "Port changed. Restarting..." progress line.

### Changed

- READMEs (EN + DE) document the v0.2.x features; relative README links became
  absolute GitHub URLs so they resolve on PyPI.

## [0.2.2] - 2026-06-23

### Fixed

- **Buttons stay disabled for the whole duration of an action.** While an
  install / start / stop / uninstall / cleanup runs, every button in the window
  is disabled - not just the action row but any transient buttons (the cleanup
  offer) too - so a second action can no longer be launched in parallel. The
  guard now walks the full widget tree (`_iter_buttons`) instead of a single
  frame, closing a gap where the cleanup-offer buttons stayed clickable during
  another action.
- **The launcher no longer disappears behind shell windows or dialogs mid
  action.** During an action the window is held `-topmost`; when the action
  finishes the flag is dropped (so it does not nag during normal use) and the
  window is raised and focused once. Window-manager quirks are swallowed so a
  `TclError` can never break an action.

## [0.2.1] - 2026-06-23

### Fixed

- **Windows: no more swarm of CMD windows during install.** Every
  `subprocess.run` / `subprocess.Popen` in the package now passes
  `CREATE_NO_WINDOW` on Windows via the new `subprocess_utils.subprocess_kwargs()`
  helper. Previously each Docker command opened a visible console window, so an
  install flashed 30-40 windows open and shut — alarming and virus-like. The
  central `actions._run` / `actions._stream_command` runners and the lockfile's
  `tasklist` probe all route through the helper; behaviour on Linux/macOS is
  unchanged (empty kwargs). A lint-style test guards against any future
  subprocess call that forgets the flag.

## [0.2.0] - 2026-06-23

### Added

- **Single-instance lockfile** (`lockfile.py`): a PID-based guard so a second
  launch is refused with an "already running" notice instead of opening a
  duplicate window. Path-driven via `LauncherConfig.lock_path`; the GUI path in
  `__main__` writes the lock on start and clears it on exit.
- **Update check** (`update_check.py`): a background GitHub Releases check that
  derives the API URL from `repo_url`, compares the latest tag against
  `LauncherConfig.app_version`, and logs an in-window note when a strictly
  newer release exists. Gated by `update_check_enabled`; completely silent on
  any network/parse error.
- **File logging** (`logging_setup.py`): a persistent rotated `launcher.log`
  plus a per-run `install.log` under the config directory, and a CWD
  `launcher-debug.log` on `--debug`. Best-effort - an unwritable directory
  degrades to fewer sinks rather than crashing the launcher.
- **PyInstaller integration** (`docker_app_launcher.pyinstaller`): a bundled
  `launcher.spec.template` with `render_spec()`, a `hidden_imports()` list, and
  build-time version injection (`write_build_info` / `read_build_info`) for
  frozen builds.
- New `LauncherConfig` fields `update_check_enabled` + `app_version`, and
  `lock_path` / `log_path` / `install_log_path` path helpers.
- `update_available` i18n string (EN/DE).
- 46 new tests (lockfile, update check, file logging, PyInstaller helpers, and
  the single-instance CLI guard); the verbose-cleanup path is now
  regression-covered.
- Enforced release gate synced from the project template: `make release-check`
  (CI + codespell + build + `twine check`), `make build-check`, and the
  `.claude/rules/release-workflow.md` release SOP. `twine` added as a dev
  dependency; richer PyPI project URLs (Documentation, Changelog).

### Changed

- `__main__` now loads the config *before* configuring logging (so the file
  sinks land under the configured directory) and routes the GUI launch through
  the single-instance lockfile guard.
- CI bumped to `actions/checkout@v7`, `actions/setup-python@v6`,
  `codecov/codecov-action@v7`.

## [0.1.0] - 2026-06-23

### Added

- `LauncherConfig` dataclass — the single, fully configurable source of truth
  (app identity, network/health, Docker timeouts, paths, GUI, links, cleanup,
  tray, i18n, lifecycle callbacks). Nothing is hard-coded.
- `launch()` / `LauncherConfig` public API and a `docker-app-launcher` CLI.
- `actions` layer (no `tkinter`): Docker checks, state detection, port probing,
  install / start / stop / uninstall (each verified), health checks, install
  manifest, and stale-artifact cleanup.
- Persistent `LauncherApp(tk.Tk)` window: one window, live streamed build
  output, inline port editing, in-window startup cleanup offer.
- Optional system tray (`docker-app-launcher[tray]`, pystray + Pillow).
- DE/EN i18n with per-app `custom_strings` overrides.
- CLI ↔ GUI parity: both route through the same actions.
- 160+ tests (no display required), mypy strict, ruff clean.

[Unreleased]: https://github.com/astrapi69/docker-app-launcher/compare/v0.13.0...HEAD
[0.13.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/astrapi69/docker-app-launcher/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/astrapi69/docker-app-launcher/compare/v0.2.0...v0.2.2
[0.2.1]: https://github.com/astrapi69/docker-app-launcher/compare/v0.2.0...8e36cd65244dbbad855e3004e4ef3ebc60424d82
[0.2.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/astrapi69/docker-app-launcher/releases/tag/v0.1.0
