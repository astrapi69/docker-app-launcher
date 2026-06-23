# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Enforced release gate synced from the project template: `make release-check`
  (CI + codespell + build + `twine check`), `make build-check`, and the
  `.claude/rules/release-workflow.md` release SOP. `twine` added as a dev
  dependency; richer PyPI project URLs (Documentation, Changelog).

### Changed

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

[Unreleased]: https://github.com/astrapi69/docker-app-launcher/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/astrapi69/docker-app-launcher/releases/tag/v0.1.0
