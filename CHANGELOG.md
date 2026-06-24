# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/astrapi69/docker-app-launcher/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/astrapi69/docker-app-launcher/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/astrapi69/docker-app-launcher/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/astrapi69/docker-app-launcher/releases/tag/v0.1.0
