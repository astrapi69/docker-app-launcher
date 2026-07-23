# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## What this is

`docker-app-launcher` — a configurable desktop launcher for Docker-based
applications. One persistent Tk window (it never closes itself); all logic is
driven by a single `LauncherConfig`, so nothing is hard-coded and the same code
base works for any Docker app.

## Layout

- `src/docker_app_launcher/`
  - `config.py` — `LauncherConfig` dataclass (the single source of truth)
  - `actions.py` — all business logic, **no `tkinter`**, fully testable
  - `ui_model.py` — framework-neutral UI behaviour (button tables, per-state
    enablement, action dispatch, close policy) shared by every frontend
  - `gui.py` — `LauncherApp(tk.Tk)`, the default `tk` frontend
  - `frontends/` — frontend registry (`gui_backend`: `tk` | `ctk` | `qt`,
    plus third-party via the `docker_app_launcher.frontends` entry-point
    group); `ctk.py` (CustomTkinter, the `ctk` extra) and `qt.py` (PySide6,
    the `qt` extra) render the same `ui_model` tables
  - `tray.py` — optional system tray (pystray + Pillow; the `tray` extra)
  - `i18n/` — string catalogs as one YAML per language (11 languages),
    `custom_strings` overrides
  - `__main__.py` — CLI entry point + GUI router
- `tests/` — pytest suite (no Docker, no display)
- `pyproject.toml` — single source of truth for metadata and tool config

## Commands

- Install: `poetry install --with dev --all-extras`
- Run everything CI runs: `make ci`
- Tests: `make test` (with coverage) or `make test-fast`
- Lint / format / types: `make lint`, `make format`, `make typecheck`
- Auto-fix: `make fix`

## Conventions

- **Nothing hard-coded:** every app-specific value (name, container/image,
  port, health endpoint, paths, timeouts) comes from `LauncherConfig`.
- **`actions.py` imports no `tkinter`** and returns `(ok, message)` tuples; it
  VERIFIES results rather than assuming success.
- **CLI ↔ GUI parity:** both call the same `actions` functions.
- **Formatting & linting:** Ruff only (no Black). Run `make fix` before committing.
- **Typing:** mypy `strict` for `src/`; tests relax `disallow_untyped_defs` only.
- **Line length:** 120.
- **i18n:** add user-facing strings to every `i18n/<code>.yaml` catalog (all
  11 languages; a parity test enforces matching keys across all of them).
- **Tests:** ≥5 tests per non-trivial action; mock Docker, never shell out.
- **Python:** target 3.10+; CI verifies 3.10 – 3.14.
