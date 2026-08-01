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

## Documentation

Two focused guides live in [`docs/`](docs/); this README is the full reference.

- **[End-user quickstart](docs/quickstart-end-user.md)** — for people who just
  want to run an app: install Docker, start the launcher, "Check system",
  install, open in the browser, plus troubleshooting organized by the eight
  problem classes the launcher reports.
- **[Consumer integration guide](docs/consumer-integration.md)** — for authors
  shipping their own app: `launcher.json` per mode, the health endpoint
  contract, ports/volumes/env, tag vs digest pinning, release artifacts (GHCR
  publish plus an optional `docker save` archive), and the update path.

## Quick Start

```bash
pip install docker-app-launcher            # tkinter window (no extra deps)
pip install "docker-app-launcher[ctk]"     # modern CustomTkinter window
pip install "docker-app-launcher[qt]"      # PySide6 (Qt) window
pip install "docker-app-launcher[tray]"    # system-tray support
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
docker-app-launcher --doctor                  # full diagnosis: config, Docker, readiness, ports, health
docker-app-launcher --status                  # state - and health, when the app is running
docker-app-launcher --health                  # probe the app's health endpoint, exit 0/1
docker-app-launcher --app-logs                # print the tail of the app container's logs
docker-app-launcher --support-bundle          # sanitized diagnosis to paste into a bug report
docker-app-launcher --install --port 9000     # build + start headless
docker-app-launcher --start                   # start the stopped app
docker-app-launcher --update                  # one step: stop, re-acquire, start, health
docker-app-launcher --stop                    # stop the running app
docker-app-launcher --uninstall               # remove containers/images
docker-app-launcher --cleanup                 # remove stale leftovers
docker-app-launcher --open                    # open the app in the browser
docker-app-launcher --debug ...               # verbose logging to stdout + launcher-debug.log
docker-app-launcher --log-level DEBUG ...     # set the log level (DEBUG, INFO, WARNING, ERROR)
docker-app-launcher --preview fresh           # open the window in a UI state, just to LOOK at it
```

**Preview** (`--preview <state>`, 0.28): opens the window in a named UI
state so its LOOK can be judged — the one thing every other check here
misses, and the reason the text-wrapping bug and the unresizable window
were found on a device rather than in CI. It touches no Docker and writes
nothing: no launcher.json, no `.env`, no pending marker. The states are
`fresh`, `busy_cancellable`, `failure_problem_card`, `guard_unavailable`,
`long_log` and `small_window`; `--help` lists them with a `[real]` or
`[fed]` marker, and the same marker is printed when the preview opens.
`[fed]` means the rendering chain is the shipped one but its input is
supplied — because producing that state for real would need Docker or a
write. Works with every `gui_backend` (tk, ctk, qt).

**Machine-readable output**: `--json` turns `--doctor`, `--status`,
`--health` and `--support-bundle` into JSON with **stable `id` fields**
(e.g. `docker_running`, `readiness_blocker`, `port_drift`,
`health_reachable`) — an API that only evolves additively:

```bash
docker-app-launcher --config launcher.json --doctor --json
# {"app": "My App", "deployment_mode": "image", "ok": false, "complete": true,
#  "problems": 1, "checks": [{"id": "docker_running", "status": "ok", ...},
#                            {"id": "image_source_declared", "status": "error", ...}]}
docker-app-launcher --config launcher.json --health --json
# {"ok": true, "detail": "reachable (HTTP 200).", "url": "http://localhost:8080/api/health"}
```

**Exit codes** (contract): `0` success · `1` operation failed / doctor
found blockers / health failed · `2` config or usage error.

**Cancelling and the concurrency guard** (0.25): running installs,
starts, updates and internal-port rebuilds show a Cancel control while
they run. A cancelled internal-port rebuild leaves the app STOPPED with
the new internal port already saved, so Start rebuilds with it. Cancel really
ends the operation where that is technically true — a cancelled pull
keeps already-downloaded layers cached (the next attempt is faster), a
cancelled update leaves the app STOPPED and says that Start runs the
previous version. Operations where a real abort is impossible show no
control. If a cancelled operation stops answering, the window frees
itself after 10 seconds with an honest note, and new operations are
paused (GUI and CLI alike) until the stuck one reports back, at most 10
minutes — restarting the launcher also clears this. Two notes you may
see and what they mean: "never confirmed its end" = the previous
operation's result is unknown, run Check system; "the concurrency guard
cannot work" = its marker file is not writable — with default settings
this should never appear.

**Which interface the app is published on** (0.26.0): in `image` and
`dockerfile` mode the launcher binds the published port to **localhost**
(`127.0.0.1`) — the app is reachable from this computer only. To expose
it on the network deliberately, set `bind_address` (e.g. `"0.0.0.0"` for
every interface, or one specific address); the launcher then logs a
warning naming what that means and how to undo it. In `compose` mode the
app's own compose file decides, so the launcher reports the actual
binding in `--doctor` (`bind_address_open`) instead of overruling it.

> The app itself has no authentication. Anyone who can reach the port can
> use it, and the launcher cannot add a lock the app does not have.

**Closing the launcher** (0.25.2): the window's X always has a way out.
When a system tray is available the X sends a RUNNING app to the tray
and the tray menu carries Quit; when no tray can dock — a frozen bundle
without the `tray` extra, or a desktop without tray support — the X
CLOSES the launcher. The app itself keeps running in Docker either way;
the launcher is a control window, not the app's host process. Quitting
while an install, update or acquisition is in flight asks first and
names what happens: the step ends, already downloaded or built parts
stay cached, nothing is deleted, and the next start shows the current
state.

**No tray icon?** The launcher says why, in the panel: either the
`tray` extra is not installed (with the pip command), or the desktop
offers no usable tray area (on GNOME the "AppIndicator and
KStatusNotifierItem Support" extension provides one). A frozen bundle
that ships without the extra on purpose says nothing — there the X
closes the launcher and the behaviour is complete, not degraded.

**Support bundle**: a human-readable document, never an opaque archive —
it states first what it contains, so you can review it before sending.
It carries versions, mode, state, port, health, the exact image identity
from the install manifest, and env **key names only** (values are never
included; key names that look like secrets are withheld).

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

**Changing ports at runtime.** The host port can be changed in every
deployment mode while the app runs: the launcher persists it and recreates
the container on the new port — without a rebuild and without re-downloading
the image, and always on the configured `bind_address` (#112). Internal ports
are **compose-only**: they are carried by the env keys the compose file reads,
so in `image` and `dockerfile` mode the launcher refuses the change and says
why — there the internal port belongs to the image itself and only a new app
version can change it.

### Deployment modes

The launcher supports three deployment modes (`deployment_mode`, #51, #78).
Which one you need, at a glance:

| Mode | Who it is for | Toolchain on the user machine |
|------|---------------|-------------------------------|
| `image` | **End users** — the consumer publishes a prebuilt image; nothing is built locally | Docker engine only (no compose, no buildx — works on old Docker generations) |
| `dockerfile` | Installs **from the source tree** — developers and everyone who wants to build locally | Docker engine + a Dockerfile in the checkout |
| `compose` | Consumers with real **multi-service stacks** (separate containers, compose networking) | Docker engine + a usable Compose frontend |

Registry pull and a local image archive are two *sources within* `image`
mode, not separate modes.

**`"compose"`** (the default — existing configs keep working unchanged):
the stack is driven through Docker Compose. The launcher detects a usable
Compose frontend before any build (#48): the Compose v2 plugin
(`docker compose`), or legacy `docker-compose` v1 when it can parse the
app's compose file. Neither present → an actionable error naming the
missing piece (Ubuntu/Debian: `sudo apt install docker-compose-plugin`)
instead of a cryptic CLI failure.

**`"dockerfile"`** — single-service apps, zero Compose dependency: the
image is built and run directly through the Docker API (docker-py). Works
on old Docker installations (20.10-era) that have no compose plugin at
all. Mode-specific fields:

```json
{
  "app_name": "My Solo App",
  "deployment_mode": "dockerfile",
  "install_dir": "/opt/my-solo-app",
  "build_context": ".",
  "dockerfile_file": "Dockerfile",
  "default_port": 8080,
  "container_port": 80,
  "container_volumes": { "my-solo-data": "/app/data" },
  "container_env": { "MY_APP_DEBUG": "false" },
  "restart_policy": "unless-stopped"
}
```

- `build_context` is relative to `install_dir`; `dockerfile_file` relative
  to the build context.
- `container_port` is the container-internal port the published host port
  maps onto (`0` = same as the host port).
- `container_volumes` are named volumes (`{volume: mount_path}`) — they
  survive rebuilds exactly like compose volumes.
- A missing Dockerfile or an unknown `deployment_mode` is a hard,
  actionable error — the launcher never guesses (#32 philosophy).

Multi-service stacks (separate frontend/backend containers, compose
networking, `depends_on` ordering) need `"compose"` mode.

**`"image"`** — prebuilt images, zero build toolchain (#78): the image is
pulled (or loaded from a local archive) and run directly through the
Docker API. Nothing is built on the user machine, so neither compose nor
buildx is needed — this is the end-user distribution mode. Mode-specific
fields:

```json
{
  "app_name": "My App",
  "deployment_mode": "image",
  "image_reference": "ghcr.io/owner/my-app:1.2.3",
  "image_archive": "images/my-app.tar",
  "default_port": 8080,
  "container_port": 80,
  "container_volumes": { "my-app-data": "/app/data" },
  "container_env": { "MY_APP_DEBUG": "false" },
  "restart_policy": "unless-stopped"
}
```

- `image_reference` (required) is a tag or a digest
  (`ghcr.io/owner/my-app@sha256:…`) — pin a digest when you want
  immutability guarantees. A missing `image_reference` is a hard error at
  config load.
- `image_archive` (optional) is a `docker save` archive. **When the file
  exists it wins** — the image is loaded from it and the registry is never
  contacted (the registry-free path). When it is configured but absent,
  the launcher falls back to pulling and the readiness gate flags the
  unreadable file, naming the directory it searched.
- A relative `image_archive` resolves against the **same base as every
  other consumer path**: `install_dir` — which, for configs loaded from a
  file, defaults to the config file's own directory. It never resolves
  against a frozen binary's unpack directory. Absolute paths are used
  as-is. Without any base, the readiness gate says so and advises setting
  `install_dir`.
- Ports, volumes, env, and `restart_policy` behave exactly as in
  dockerfile mode.
- The image is fetched on **install and explicit start only** — never
  silently in the background. Pinned digests never change.
- **Updating** is a single step: the **Update** button, or
  `--update`, runs stop → re-pull → start → health, with named volumes
  preserved and the previous image kept for rollback. (Equivalent to the
  older **Stop, then Start**, which still works.)
- **Offline:** if the registry is unreachable but the image is already
  local, the start proceeds on the local image. If it is missing locally,
  the launcher names the network requirement up front instead of failing
  mid-way.
- Registry credentials are **not** touched by default (#77);
  `use_registry_credentials: true` opts in for private registries.
- Multi-arch images are resolved to the machine's platform by the engine
  itself; if the publisher shipped no variant for this platform, the
  launcher reports exactly that instead of a raw library error.

See the **[consumer integration guide](docs/consumer-integration.md)** for
publishing images, the health endpoint contract, pinning, and release
artifacts in one place.

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
- Installation assistant: "Check system" with a per-problem card
  ("What does this mean?" / "What you can do"), copy diagnosis /
  support bundle, collapsed details log (0.24)
- One-step Update (button + `--update`) with rollback hint (0.24)
- Machine-readable diagnostics: `--doctor/--status/--health/--support-bundle --json` (0.24)
- Cancel for running operations with honest per-operation semantics,
  and a defined idle state after success, failure and cancel (0.25)
- Cross-process concurrency guard: no two operations on the same
  container, with named exits - never a stuck state (0.25)

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

In **image mode** the manifest additionally records which exact image the
install runs and where it came from — the support-diagnosis source of
truth: `image_reference` (the configured tag/digest), `image_id` (the
resolved engine image ID), `image_digests` (repo digests, when the
registry provided them), and `image_source` (`registry` or `archive`,
by the same rule the acquisition uses; a start that fell back to a local
image while the registry was unreachable still records `registry`).
Identity fields are omitted rather than guessed when the engine is
unreachable; older manifests without these keys stay valid.

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

## GUI Frontends

The window is rendered by a swappable frontend, selected with the
`gui_backend` config field. All frontends share the same behaviour tables
(`ui_model.py`), so button layout, per-state enablement, tooltips, and close
behaviour are identical by construction — only the widget toolkit differs.

| `gui_backend` | Toolkit | Install |
|---------------|---------|---------|
| `"tk"` (default) | tkinter (stdlib) | nothing extra |
| `"ctk"` | CustomTkinter — modern look, follows OS dark/light | `pip install "docker-app-launcher[ctk]"` |
| `"qt"` | PySide6 (Qt) — native widgets | `pip install "docker-app-launcher[qt]"` |

```json
{ "app_name": "My App", "gui_backend": "ctk" }
```

Third-party packages can register additional frontends via the
`docker_app_launcher.frontends` entry-point group; any module exposing
`run(config, *, debug=False) -> int` qualifies.

## Architecture

| Module        | Responsibility                                              |
|---------------|-------------------------------------------------------------|
| `config.py`         | `LauncherConfig` dataclass — the single source of truth.    |
| `actions.py`        | All business logic. No `tkinter`. Fully testable.           |
| `ui_model.py`       | Framework-neutral UI behaviour shared by every frontend.    |
| `gui.py`            | `LauncherApp(tk.Tk)` — the default `tk` frontend.            |
| `frontends/`        | Frontend registry + `ctk` (CustomTkinter) and `qt` (PySide6). |
| `tray.py`           | Optional system tray (pystray + Pillow).                     |
| `i18n/`             | One YAML catalog per language (11 languages), custom-string overrides. |
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
make ci           # lint + format-check + typecheck + tests
make test         # tests with coverage
make test-gui     # real-window GUI tests (needs a display or xvfb-run)
make screenshots  # dark-mode screenshots of all three frontends -> test-screenshots/
make fix          # auto-fix lint + format
```

### Integration runs against a real engine

The mocked suite cannot catch cross-layer breaks by nature — two opt-in
runners prove the real thing (both need a running local Docker daemon):

```bash
# The image mode's old-engine promise, MEASURED (#84): starts a PINNED
# docker:20.10.24 daemon, PROVES it has no compose plugin and no buildx,
# then pulls AND archive-loads a prebuilt image, starts it, checks HTTP.
tests/integration/run_image_mode_old_engine_integration.sh

# The full lifecycle matrix (#79): install, install-again, logs, stop,
# restart of the stopped stack, uninstall, nothing-runs transitions —
# for image, dockerfile AND compose mode (compose needs the v2 plugin).
tests/integration/run_lifecycle_matrix_integration.sh
# narrow to one mode:
DAL_LIFECYCLE_MATRIX_MODE=image tests/integration/run_lifecycle_matrix_integration.sh
```

The old-engine cell pulls a generic public image by default, so the suite
never depends on a consumer release being published. To measure it once
against YOUR published image instead (#89), point the three knobs at it —
the cell prints the reference, the resolved digest, the image size, the
port and the response body, so a run whose override did not take effect is
distinguishable from a real one:

```bash
DAL_OLD_ENGINE_GHCR_REF="ghcr.io/owner/app:1.2.3" \
DAL_OLD_ENGINE_GHCR_CONTAINER_PORT=18001 \
DAL_OLD_ENGINE_GHCR_PATH=/api/health \
  tests/integration/run_image_mode_old_engine_integration.sh -s
```

CI split: every push runs the mocked suite plus the old-engine cell; the
full lifecycle matrix runs nightly (`lifecycle-matrix.yml`) and on
demand via the Actions tab — a green push alone does not imply
full-matrix coverage.

### Manual launcher testing

Sample configs under `test-configs/` let you drive the launcher against a
real app config without writing one. Their `install_dir` is RELATIVE and
resolves against the config file itself (`"../../adaptive-learner"` expects
the app repo checked out next to this one); `repo_url` is only used for
links/update checks — the launcher does not clone it (#74 tracks that
feature decision). The `launcher-*` targets read
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
# harmless
