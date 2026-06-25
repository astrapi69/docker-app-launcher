# docker-app-launcher

docker-app-launcher is a configurable, cross-platform desktop launcher for
Docker-based apps - one persistent GUI window that starts your containers,
streams the build progress line-by-line, and never closes itself.
Pip-installable, no Electron, Linux/macOS/Windows, 11-language UI.

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

- One persistent window (never closes itself)
- Real-time progress bar with Docker build step parsing
- Docker check on startup
- Live build progress (streamed line by line)
- Configurable port (GUI + CLI) with live validation
- Expert internal ports (collapsible)
- 3 states: not installed / running / stopped
- Install manifest for precise cleanup
- Startup cleanup (active volumes excluded)
- System tray with AppIndicator (Linux/Wayland) + taskbar fallback
- "Run in background" button
- Custom window + tray icons
- Language picker with OS auto-detection (11 languages)
- Single-instance lockfile
- Persistent file logging with rotation
- Verbose uninstall with per-step verification
- Update checker via GitHub Releases API
- DE/EN + 9 additional languages (YAML-based, extensible)
- Actions architecture (testable without GUI)
- CLI ↔ GUI parity

## Custom Icons

Configure window and system tray icons:

```python
launch(LauncherConfig(
    app_name="My App",
    icon_path="path/to/app-icon.png",         # Window icon
    tray_icon_path="path/to/tray-icon.png",   # Tray icon (optional, falls back to icon_path)
))
```

```json
{
  "icon_path": "branding/my-app-icon.png",
  "tray_icon_path": "branding/my-app-tray.png"
}
```

If no icon is configured, a default icon with the app's initial letter is generated automatically.

Supported formats: PNG (recommended), ICO, BMP. Recommended size: 256x256 (window), 64x64 (tray).

## Cleanup Configuration

Configure which paths are searched for stale artifacts:

```python
launch(LauncherConfig(
    app_name="My App",
    container_name="my-app",
    legacy_names=["old-app-name", "prototype-v1"],
    cleanup_configs=[
        "~/.old-app-name",
        "~/.config/old-app-name",
        "~/.local/share/old-app-name",
    ],
    cleanup_search_paths=[
        "~/.config/",
        "~/.local/share/",
        "~/",
    ],
))
```

```json
{
  "legacy_names": ["old-app-name"],
  "cleanup_configs": [
    "~/.old-app-name",
    "~/.config/old-app-name"
  ],
  "cleanup_search_paths": [
    "~/.config/",
    "~/.local/share/",
    "~/"
  ]
}
```

- `legacy_names`: Previous project names to find stale containers/images/volumes.
- `cleanup_configs`: Explicit config directories to offer for removal.
- `cleanup_search_paths`: Base directories searched for `legacy_names` subdirectories (`<base>/<name>` and `<base>/.<name>`).
- Active project volumes are automatically excluded from cleanup.
- User-data volumes are unchecked by default (opt-in deletion).

## Configuration Paths

All launcher state is stored under `config_dir` (default: `~/.{app_slug}/`):

```
~/.my-app/
  launcher.json          # Port, settings, preferences
  .env                   # Docker Compose port variables
  install-manifest.json  # Installed containers, images, history
  launcher.log           # Persistent log (rotated, 5MB max)
  install.log            # Last install/rebuild log
  launcher.lock          # Single-instance lockfile
```

Override the config directory:

```python
launch(LauncherConfig(
    config_dir="~/.custom-path/my-app",
))
```

## Install Manifest

The launcher automatically maintains an install manifest at `{config_dir}/install-manifest.json`. This file tracks every artifact created during installation, enabling precise cleanup without guesswork.

```json
{
  "installed_at": "2026-06-24T14:30:00Z",
  "updated_at": "2026-06-24T18:15:00Z",
  "app_name": "My App",
  "app_version": "1.95.0",
  "launcher_version": "0.5.0",
  "port": 8501,
  "compose_project": "my-app",
  "compose_file": "/home/user/my-app/docker-compose.prod.yml",
  "containers": [
    {"name": "my-app-frontend", "image": "my-app-frontend:latest"},
    {"name": "my-app-backend", "image": "my-app-backend:latest"}
  ],
  "images": [
    "my-app-frontend:latest",
    "my-app-backend:latest"
  ],
  "volumes": [
    "my-app-data"
  ],
  "install_history": [
    {"action": "install", "version": "1.94.0", "at": "2026-06-20T10:00:00Z"},
    {"action": "update", "version": "1.95.0", "at": "2026-06-24T14:30:00Z"}
  ]
}
```

The manifest is:
- **Written** after every successful install or start (with rebuild).
- **Updated** with version and timestamp on each start.
- **Appended** to `install_history` for every install/update/uninstall.
- **Marked** as uninstalled (not deleted) on deinstallation.

### How cleanup uses the manifest

With a manifest, cleanup knows exactly which containers, images and volumes belong to the current or previous installation. Without a manifest (legacy installs), it falls back to pattern-matching against `container_name` and `legacy_names`.

```
Cleanup with manifest:    Precise — removes listed artifacts only
Cleanup without manifest: Pattern-based — searches by name patterns
```

This is why the manifest is created automatically and should not be deleted manually.

## Progress Bar

The launcher shows a real-time progress bar during installation, startup, cleanup, and uninstall.

During Docker builds, progress is parsed from the build output (step N/M). Configure an estimate for the initial build:

```json
{
  "estimated_build_steps": 38
}
```

Set to 0 (default) for auto-detection from Docker output.

## Language Selection

The launcher auto-detects your system language. A dropdown lets you switch at any time. Supported: Deutsch, English, Ελληνικά, Español, Français, हिन्दी, 日本語, 한국어, Português, Türkçe, Bahasa Indonesia.

```json
{
  "locale": "auto"
}
```

`"auto"` detects the OS language. Set a specific code (`"de"`, `"en"`, `"ja"`, ...) to override.

## Single Instance

Prevents launching multiple instances simultaneously.

```json
{
  "single_instance": true
}
```

## Logging

The launcher writes persistent logs for diagnostics:

```
~/.my-app/
  launcher.log    # Persistent, rotated (default 5 MB, 3 backups)
  install.log     # Overwritten per install/rebuild
```

With `--debug`: an additional `launcher-debug.log` in the current directory.

```json
{
  "log_level": "INFO",
  "log_max_size": 5000000,
  "log_backup_count": 3
}
```

## Cleanup Safety

The startup cleanup automatically excludes active project volumes. Only stale artifacts from previous or legacy installations are offered for removal.

Skipped items are logged explicitly:

```
Volume 'my-app-data' skipped (active project)
Volume 'old-app-data' removing... ✓
```

## Docker Check

The launcher checks Docker availability at startup with platform-specific diagnostics, and offers the right next action (start the daemon / Desktop, or open the install guide):

| Platform | Checks | Start action |
|----------|--------|-------------|
| Linux | docker binary + systemd daemon + group membership | `systemctl start docker` (via `pkexec`) |
| Windows | docker binary + Docker Desktop path + daemon | Launches `Docker Desktop.exe` |
| macOS | docker binary + Docker.app + daemon | `open /Applications/Docker.app` |

Override the Docker Desktop path or install URL:

```json
{
  "docker_desktop_path": "/custom/path/Docker Desktop.exe",
  "docker_install_url": "https://my-company.com/docker-setup"
}
```

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

### Manual launcher testing

Sample configs under `test-configs/` let you drive the launcher against a
real app config without writing one. The `launcher-*` targets read
`TEST_CONFIG` (default `test-configs/adaptive-learner.json`):

```bash
make launcher-test               # open the GUI in debug mode
make launcher-status             # print the app state and exit
make launcher-check              # check Docker availability and exit
make launcher-stop               # stop the app
make launcher-cleanup            # remove stale leftovers
make launcher-version            # print the launcher version

# pick a bundled config explicitly
make launcher-test-al            # test-configs/adaptive-learner.json
make launcher-test-bibliogon     # test-configs/bibliogon.json
make launcher-test-minimal       # test-configs/minimal.json

# or point at any config
make launcher-test TEST_CONFIG=path/to/your.json

make smoke                       # version + every test-config parses + --check
```

## Used by

- [Adaptive Learner](https://github.com/astrapi69/adaptive-learner) — AI-powered language learning platform
- [Bibliogon](https://github.com/astrapi69/bibliogon) — React-based book authoring platform

## License

[MIT](https://github.com/astrapi69/docker-app-launcher/blob/main/LICENSE) © Asterios Raptis
