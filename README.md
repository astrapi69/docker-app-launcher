# docker-app-launcher

Configurable desktop launcher for Docker-based applications.
**One persistent window.** It opens, shows progress, and never closes itself —
no dialog chains.

[![CI](https://github.com/astrapi69/docker-app-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/astrapi69/docker-app-launcher/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/docker-app-launcher.svg)](https://pypi.org/project/docker-app-launcher/)
[![Python](https://img.shields.io/pypi/pyversions/docker-app-launcher.svg)](https://pypi.org/project/docker-app-launcher/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/astrapi69/docker-app-launcher/blob/main/LICENSE)

> 🇩🇪 [Deutsche Version](https://github.com/astrapi69/docker-app-launcher/blob/main/README-de.md)

## Quick Start

```bash
pip install docker-app-launcher
```

### Python API (3 lines)

```python
from docker_app_launcher import LauncherConfig, launch

launch(LauncherConfig(
    app_name="My App",
    container_name="my-app",
    default_port=8080,
))
```

### CLI

```bash
docker-app-launcher --config launcher.json   # open the window
docker-app-launcher --version                 # print the launcher version and exit
docker-app-launcher --check                   # is Docker running?
docker-app-launcher --status                  # print state and exit
docker-app-launcher --install --port 9000     # build + start headless
docker-app-launcher --start                   # start the stopped app
docker-app-launcher --stop                    # stop the running app
docker-app-launcher --uninstall               # remove containers/images
docker-app-launcher --cleanup                 # remove stale leftovers
docker-app-launcher --open                    # open the app in the browser
docker-app-launcher --debug ...               # verbose logging to stdout + launcher-debug.log
```

### launcher.json

Everything is configurable. Only `app_name` is required — the rest is derived
(slug, container/image names, compose project, config dir) or defaulted.

```json
{
  "app_name": "My App",
  "container_name": "my-app",
  "default_port": 8080,
  "compose_file": "docker-compose.prod.yml",
  "install_dir": "/opt/my-app",
  "health_check_path": "/api/health",
  "health_check_key": "status",
  "health_check_value": "ok",
  "repo_url": "https://github.com/owner/repo",
  "app_version": "0.4.0",
  "update_check_enabled": true,
  "internal_ports": { "nginx": 80 },
  "env_internal_port_keys": { "nginx": "NGINX_PORT" },
  "show_advanced_ports": true,
  "locale": "en"
}
```

> `internal_ports`, `env_internal_port_keys`, and `show_advanced_ports` are
> optional expert fields — omit them and the launcher behaves exactly as before
> (single host port, no advanced panel).

## Features

- **One persistent window** — never closes itself; only the X closes it.
- **Docker check on startup** — distinguishes *not installed* / *running* / *stopped* / *no Docker*.
- **Live build progress** — the Docker build is streamed line-by-line into the window.
- **Configurable port** — editable in the GUI and via `--port`, validated and persisted (to `launcher.json` and the `.env` next to the compose file, so the launcher and Compose can't disagree).
- **Live port changes** — the port field stays editable while the app runs; "Apply port" validates, rewrites `.env`, and recreates the stack in seconds (no rebuild — only the published host port changed), then health-checks on the **new** port.
- **Advanced internal ports** (experts) — optional `internal_ports` / `env_internal_port_keys` let you remap in-container ports (full 1–65535 range, e.g. nginx `:80`); a collapsed "Advanced settings" panel (gated by `show_advanced_ports`) applies them with an image rebuild + health check. Empty by default: no extra `.env` keys, no UI, no behaviour change.
- **Verified actions** — install runs a health check; uninstall re-lists the containers to confirm they are gone.
- **Install manifest + startup cleanup** — finds and offers to remove stale leftovers of older installs.
- **Verbose uninstall / cleanup** — every step reported with a ✓ / ✗ result.
- **Single-instance guard** — a PID-based lockfile refuses a second launch with an "already running" notice instead of opening a duplicate window.
- **Background update check** — checks GitHub Releases (derived from `repo_url`) and notes in-window when a newer release exists. Opt-out via `update_check_enabled`; silent on any network error.
- **File logging** — a rotated `launcher.log` plus a per-run `install.log` under the config dir, and a `launcher-debug.log` on `--debug`. Best-effort: an unwritable dir degrades gracefully rather than crashing.
- **Concurrency-safe UI** — every button is disabled for the full duration of an action and the window is held on top, so a second action can't be launched in parallel.
- **Quiet on Windows** — every Docker subprocess runs with `CREATE_NO_WINDOW`, so an install no longer flashes a swarm of console windows (no change on Linux/macOS).
- **PyInstaller-ready** — a bundled spec template, hidden-imports list, and build-time version injection for shipping frozen single-file builds.
- **System tray + "Run in background"** (optional) — `pip install docker-app-launcher[tray]`; while running, the window minimizes to the tray (a visible **Run in the background** button and the X both use it). When the tray can't dock it falls back to a taskbar-minimized window, so it never silently closes.
  - **Linux note:** the reliable tray uses pystray's **AppIndicator** backend, which needs `gi` (PyGObject) plus the AppIndicator typelib. The `[tray]` extra pip-installs PyGObject (Linux-only; needs `libgirepository1.0-dev`, `libcairo2-dev`, `pkg-config` to build), and you also need the typelib at runtime — on Ubuntu: `sudo apt install gir1.2-ayatanaappindicator3-0.1`. If you instead rely on the system `python3-gi` (apt), create the venv with `--system-site-packages` so `gi` is importable. Without any of this the launcher still works — it just minimizes to the taskbar. Run with `--debug` to see which backend was selected.
- **DE / EN i18n (YAML)** — strings live in per-language YAML files (`i18n/de.yaml`, `i18n/en.yaml`) loaded at startup; **add a language by dropping a `<code>.yaml` file** beside them. German uses real UTF-8 umlauts. Per-app overrides via `custom_strings`.
- **Actions architecture** — all business logic is GUI-free and unit-tested without a display.
- **CLI ↔ GUI parity** — both call the exact same actions layer.

## Architecture

| Module        | Responsibility                                              |
|---------------|-------------------------------------------------------------|
| `config.py`         | `LauncherConfig` dataclass — the single source of truth.    |
| `actions.py`        | All business logic. No `tkinter`. Fully testable.           |
| `gui.py`            | `LauncherApp(tk.Tk)` — a thin window over the actions.       |
| `tray.py`           | Optional system tray (pystray + Pillow).                     |
| `i18n.py`           | DE/EN strings with custom-string overrides.                  |
| `lockfile.py`       | PID-based single-instance guard.                            |
| `update_check.py`   | Background GitHub Releases update check.                    |
| `logging_setup.py`  | Rotated file logging (`launcher.log` / `install.log`).      |
| `subprocess_utils.py` | Windows `CREATE_NO_WINDOW` kwargs for all subprocesses.   |
| `pyinstaller/`      | Spec template + helpers for frozen builds.                  |
| `__main__.py`       | CLI entry point + GUI router.                                |

## Configuration reference

See [LauncherConfig](https://github.com/astrapi69/docker-app-launcher/blob/main/src/docker_app_launcher/config.py) for the full field list
(app identity, network/health, Docker timeouts, paths, GUI, links, cleanup,
tray, i18n, and lifecycle callbacks).

## Development

```bash
poetry install --with dev --all-extras
make ci        # lint + format-check + typecheck + tests
make test      # tests with coverage
make fix       # auto-fix lint + format
```

## Used by

- [Adaptive Learner](https://github.com/astrapi69/adaptive-learner)

## License

[MIT](https://github.com/astrapi69/docker-app-launcher/blob/main/LICENSE) © Asterios Raptis
