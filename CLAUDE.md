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
      dispatches per `deployment_mode` (compose | dockerfile | pull, #51/#78)
    - `compose_runtime.py` — which Compose frontend is usable (plugin /
      legacy v1 / none), cached per process (#48)
    - `tool_versions.py` — engine/CLI/compose/buildx versions (parsed via
      `packaging`), cached + logged as one chain line; the intrinsic buildx
      floor (#54)
    - `build_readiness.py` — the per-mode build capability gate: collect
      every missing/too-old link BEFORE the build, intrinsic + app-declared
      minimums, source-attributed (#54)
    - `dockerfile_backend.py` — single-service build/run via docker-py,
      zero compose dependency (#51)
    - `pull_backend.py` — prebuilt-image pull/load + run via the engine
      API, zero build toolchain (#78); archive source wins over registry
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
  - `snap.py` — Snap-confinement detection + startup warning (#63)
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

## Readiness Rules and Error Classes

Recorded error classes (a fix is not done until it is measured against these):

- **Present is not functional.** A check that only proves an artifact EXISTS
  (binary there, file there, plugin there) proves nothing about the
  capability actually needed. Precedents: the compose plugin was present but
  the build was impossible because buildx was 0.8.2 (#54); the launcher
  config was found but loaded from the wrong path (frozen-branding bug, #32);
  a compose file was referenced but not shipped in the bundle. Every
  readiness check must verify the needed CAPABILITY, not its proxy. Related to
  the "CI green, actually broken" and "the push never landed" classes.
- **Piecemeal discovery is a design defect, not bad luck.** Preconditions are
  checked COMPLETELY and BEFORE the expensive operation, with a COLLECTING
  message that names every missing/too-old link at once. A minutes-long build
  must never fail on a precondition that was knowable up front, and a user
  must never have to fail, fix, and retry N times to discover N gaps.
- **Switching from CLI to SDK inherits different configuration behavior.**
  What the CLI treats leniently, the library may throw as a hard error.
  Precedent (#77): a stale ``credsStore: gcloud`` in ``~/.docker/config.json``
  is shrugged off by ``docker build`` but hard-fails docker-py's build with
  ``StoreError``, because the SDK eagerly resolves credentials for ALL
  configured registries. On every CLI->SDK migration, audit which user
  configuration the library reads ON ITS OWN and how it reacts to
  incompleteness - and default to NOT triggering resolution the launcher
  does not need (``use_registry_credentials`` opt-in).
- **A declared follow-up is not tracking.** An intent written as "follow-up"
  in release notes, or "remains open" inside an issue that can be closed,
  dies with its carrier - both happened: the `stream_app_logs()` GUI wiring
  (declared in the 0.17.0 notes, no issue until #72) and the signing
  decision (marked "remains open" in #58, which was then closed; carrier is
  now #73). Every open intent gets its OWN issue at the moment it is
  deferred, never a note in prose. Corollary: an empty backlog proves only
  that nothing KNOWN is open - completeness claims need a sweep (release
  notes, closed-issue texts, "follow-up" grep), not a backlog glance.

Concretely: the build paths go through one capability gate per mode
(`docker/build_readiness.py`) that collects all blockers before the build
(`compose_blockers` / `dockerfile_blockers` / `pull_blockers`), never a
chain of independent green checkmarks.

### Mode completeness rule (#78)

Every deployment mode multiplies the maintenance surface. A mode counts as
SUPPORTED only when it appears in ALL of the following; missing any one
means it is supported on paper and checks nothing in the field — exactly
the state the error classes above are directed against:

1. Readiness gate as a CAPABILITY check (its own `*_blockers` collector,
   not existence proxies).
2. Its own line(s) in `--doctor`.
3. Integration tests for the full operation set (acquire/start/stop/
   remove/logs/state) against a real engine, or a named cell in the
   environment-matrix manual checklist when not containerizable.
4. The full test contract (RED-first, mocked unit suite, proof of the
   checked set, message-visibility tests).
5. Config validation with a hard error at `resolve()` on an inconsistent
   mode config (e.g. `pull` without `image_reference`).
6. A README section with a working example config.
7. An entry in `docs/environment-matrix.md` (supported cells + test cell).

This rule applies to every FUTURE mode as well; adding a mode without all
seven items is an incomplete change, not a smaller one. Gaps found in
EXISTING modes get their own issues at the moment they are found.

### Docker requirement sources (intrinsic vs app-declared)

Two sources, kept separate and attributed in every message:

- **Intrinsic (launcher):** what the launcher needs for the chosen mode.
  Non-negotiable, lives in code (compose mode: buildx >= 0.17 once compose is
  new enough to gate it; dockerfile mode: a reachable engine API). Minimums
  are BACKED (compose source / release notes), never set from memory.
- **App-declared (config):** what the app's own Dockerfile / compose file
  demands - `min_engine_version`, `min_api_version`, `min_compose_version`,
  `min_buildx_version` (all optional). Effective requirement = MAX(intrinsic,
  declared): the config can only RAISE the bar. A declared value below the
  intrinsic floor is warned about and the intrinsic value wins. Prefer
  `min_api_version` for engine features (the API version is the more robust
  signal). Version strings are dirty in the real world (`20.10.21+dfsg1`,
  `v0.8.2-docker`): normalize, then compare with a real version library
  (`packaging`), never a string compare or a home-grown parser. Unparsable
  declared versions are a hard error at `resolve()`.
