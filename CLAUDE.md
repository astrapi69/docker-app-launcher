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
  - `actions.py` — FACADE: re-exports the public API; the code lives in:
  - `docker/` — everything Docker, one concern per module:
    - `detection.py` — is Docker usable here (checks, context sweep, errno
      socket probe, daemon/Desktop start, group self-repair)
    - `lifecycle.py` — install/start/stop/uninstall/health/get_state;
      dispatches per `deployment_mode` (compose | dockerfile, #51)
    - `compose_runtime.py` — which Compose frontend is usable (plugin /
      legacy v1 / none), cached per process (#48)
    - `dockerfile_backend.py` — single-service build/run via docker-py,
      zero compose dependency (#51)
    - `py_client.py` — native Docker API access, typed exception
      classification (#44)
    - `cleanup.py` — find + remove leftovers of previous installs
    - `inventory.py` — which docker objects belong to this app (read-only)
    - `command_runner.py` — shared subprocess/streaming layer, DOCKER_HOST override
  - `launcher_settings.py` — launcher.json/.env persistence (ports, locale, geometry)
  - `install_manifest.py` — what we installed, for precise cleanup
  - `ui_model.py` — framework-neutral UI behaviour shared by every frontend
  - `gui.py` — FACADE for the old import path; the Tk window lives in:
  - `frontends/` — registry (`gui_backend`: `tk` | `ctk` | `qt`) plus one
    window per file: `tk_window.py`, `ctk_window.py`, `qt_window.py`,
    `tooltip.py`
  - `tray.py` — optional system tray (pystray + Pillow; the `tray` extra)
  - `i18n/` — string catalogs as one YAML per language (11 languages)
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

## Naming and Architecture Rules

- Filenames must be self-documenting and intention-revealing. Avoid generic names like utils, helper, or script. The name must precisely describe the file's purpose or content. Apply Clean Code naming principles strictly.
- Enforce the Single Responsibility Principle at the file level. Each file should contain exactly one primary class or a tightly coupled set of functions. The filename must describe this primary responsibility. Do not force exact class-name matching if it violates Python module conventions.

## Logging and Diagnostics Rules

- The launcher must never swallow messages. All stdout, stderr, and exception output must be visible to the user either in a dedicated log panel or in a persistent log file.
- Every subprocess call must capture stdout and stderr and forward them to the logging system. Silent subprocess execution is forbidden.
- The Tk mainloop must not silently discard exceptions. Install a global exception handler (tk.Tk.report_callback_exception) that logs the full traceback and surfaces a user-visible error dialog.
- Docker container logs must be tailable from the GUI. Provide a mechanism to stream docker logs output into the UI without blocking the main thread.
- Configure the Python logging module at application startup with at least two handlers: a console handler and a rotating file handler. The log file location must follow platformdirs conventions.
- Provide a configurable log level (DEBUG, INFO, WARNING, ERROR) via environment variable or config file. The default must be INFO.
- print() statements are forbidden in production code. Use the logging module exclusively.
- Every fix for message visibility must include a test that verifies the output reaches the logging system.
