# docker-app-launcher

Configurable desktop launcher for Docker-based applications.
**One persistent window.** It opens, shows progress, and never closes itself —
no dialog chains.

[![CI](https://github.com/astrapi69/docker-app-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/astrapi69/docker-app-launcher/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/docker-app-launcher.svg)](https://pypi.org/project/docker-app-launcher/)
[![Python](https://img.shields.io/pypi/pyversions/docker-app-launcher.svg)](https://pypi.org/project/docker-app-launcher/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🇩🇪 [Deutsche Version](README-de.md)

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
docker-app-launcher --status                  # print state and exit
docker-app-launcher --install --port 9000     # build + start headless
docker-app-launcher --check                   # is Docker running?
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
  "locale": "en"
}
```

## Features

- **One persistent window** — never closes itself; only the X closes it.
- **Docker check on startup** — distinguishes *not installed* / *running* / *stopped* / *no Docker*.
- **Live build progress** — the Docker build is streamed line-by-line into the window.
- **Configurable port** — editable in the GUI and via `--port`, validated and persisted (to `launcher.json` and `.env`).
- **Verified actions** — install runs a health check; uninstall re-lists the containers to confirm they are gone.
- **Install manifest + startup cleanup** — finds and offers to remove stale leftovers of older installs.
- **Verbose uninstall / cleanup** — every step reported with a ✓ / ✗ result.
- **System tray** (optional) — `pip install docker-app-launcher[tray]`; the window minimizes to the tray while running.
- **DE / EN i18n** — with per-app custom-string overrides via `custom_strings`.
- **Actions architecture** — all business logic is GUI-free and unit-tested without a display.
- **CLI ↔ GUI parity** — both call the exact same actions layer.

## Architecture

| Module        | Responsibility                                              |
|---------------|-------------------------------------------------------------|
| `config.py`   | `LauncherConfig` dataclass — the single source of truth.    |
| `actions.py`  | All business logic. No `tkinter`. Fully testable.           |
| `gui.py`      | `LauncherApp(tk.Tk)` — a thin window over the actions.       |
| `tray.py`     | Optional system tray (pystray + Pillow).                     |
| `i18n.py`     | DE/EN strings with custom-string overrides.                  |
| `__main__.py` | CLI entry point + GUI router.                                |

## Configuration reference

See [LauncherConfig](src/docker_app_launcher/config.py) for the full field list
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

[MIT](LICENSE) © Asterios Raptis
