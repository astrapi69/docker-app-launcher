# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Per-mode lifecycle matrix against a real engine (#79).** For image,
  dockerfile and compose mode, the FULL operation set — install,
  install-when-installed, logs, stop, restart of the stopped stack,
  uninstall, and the nothing-runs transitions — now runs against a real
  daemon (`run_lifecycle_matrix_integration.sh`), with the checked
  operation set enumerated and asserted complete per mode. Runtime
  split, documented: every push runs the mocked suite plus the fast
  old-engine cell; the full matrix runs nightly and on demand
  (`lifecycle-matrix.yml`). First full run 2026-07-29: 3/3 modes green.
- **Coverage check for path-bearing config fields (#85).** Every
  path-suggestive `LauncherConfig` field must be classified by its
  anchoring; base_dir-classified fields get their resolution proven for
  explicit `install_dir`, file-loaded configs, and the flagged cwd
  fallback. A new path field that skips classification fails the suite
  (rule in CLAUDE.md, precedent #83).
- **The image mode's old-engine promise is now MEASURED, not argued
  (#84).** A new CI job (`image-mode-old-engine`) runs both acquisition
  sources — registry pull and local archive load, each followed by a
  container start and an HTTP check — against a PINNED
  `docker:20.10.24-dind` engine (API 1.41) that the runner first proves
  free of any compose plugin, legacy docker-compose, and buildx (the dind
  convenience image bundles the client plugins; the cell strips them to
  model the real `docker.io` distro profile). First measurement
  2026-07-29: green. This resolves the "Known limitation" stated in the
  0.23.0 notes and unblocks consumers switching their distribution to the
  image mode.

## [0.23.0] - 2026-07-29

### Added

- **Third deployment mode `image` (#78, #82).** For consumers that publish a
  PREBUILT image: the launcher pulls (or loads) and starts it via the
  engine API — nothing is built on the user machine, so neither compose
  nor buildx is needed and old Docker generations are supported cells.
  New config fields: `image_reference` (tag or digest, required in image
  mode — hard error at config load when missing) and optional
  `image_archive` (a `docker save` file; when present it wins and the
  registry is never contacted). Pull progress streams layer-by-layer into
  the log panel; fetching happens on install and explicit start only.
  Offline: a locally present image starts without network (registry
  unreachable → documented local-image fallback); a missing one gets the
  named network pre-warning before any attempt. A multi-arch image with
  no variant for the machine's platform yields a clear publisher-facing
  message instead of a raw library error. Registry credentials stay
  untouched by default (#77; `use_registry_credentials` opts in). An
  archive that does not contain `image_reference` is a hard, named error.
  Readiness gate (`image_blockers`), `--doctor`, and all 11 i18n catalogs
  extended; README documents which audience needs which of the three
  modes.
- **Mode completeness rule (CLAUDE.md).** A deployment mode counts as
  supported only when it appears in readiness gate, `--doctor`,
  integration tests, the full test contract, config validation, README,
  and the environment matrix — gaps in existing modes are tracked as #79
  (per-mode lifecycle integration matrix) and #80 (image-mode manifest detail).

### Changed

- **The mode is named `image`, not `pull` (#82).** `compose` and
  `dockerfile` name the artifact that drives the mode; `pull` named an
  action — one the mode documentedly does not perform when `image_archive`
  is set. The config fields already said it (`image_reference`,
  `image_archive`). Renamed before the first release, so no consumer
  config ever used the old name.

### Known limitation

- **The old-engine cell is documented but not yet automated (#79).** The
  image mode's central promise — pull + run on a Docker generation with
  no compose and no buildx — is so far backed by the API surface argument
  (the path uses only `/images/create` and the containers API) and a live
  proof against a current engine (registry pull + archive load + run +
  HTTP check). A containerized old-generation engine cell is named in
  `docs/environment-matrix.md` and tracked in #79; consumers should wait
  for it before switching their distribution to this mode.

### Fixed

- **A relative `image_archive` resolves via the shared base rule (#83).**
  The archive path had its own inline base (install_dir or cwd) — the
  #64/#2120 class where a frozen binary resolves against its unpack
  directory. It now anchors to the same `_base_dir()` as the compose file
  and the build context (file-loaded configs anchor to the config file's
  directory), and the readiness gate names the searched directory — or
  the missing `install_dir` on the cwd fallback (new i18n key, all 11
  catalogs).

- **Pull path no longer trips over the #77 auth sentinel.** docker-py's
  pull wraps `_auth_configs` in its dict-based `AuthConfig`; the sentinel
  is now a real `{"auths": {}}` dict (still non-empty, still helper-free),
  fixing "argument of type '_NoRegistryAuth' is not iterable" — found by
  the #78 live proof against a real daemon, invisible to the mocked suite.

## [0.22.0] - 2026-07-29

### Added

- **`--doctor` (#75, #76).** One diagnostic pass: config identity + files,
  daemon, toolchain versions, every collected readiness blocker (incl. the
  rendered-port check), launcher port/env_port_key — and for an ALREADY
  RUNNING stack the published-port drift check plus the real health probe
  with its URL, which plain `--status` hid. Exit 0/1; made for running
  locally before every wrapper release.
- **Live log follow in the GUI (#72).** While the stack runs, the App-logs
  button starts a live follow (name-prefixed lines streaming into the log
  panel) and flips to "Stop logs"; a second click, leaving the running
  state, or closing the window stops it. All three frontends; the one-shot
  tail stays for stopped stacks. Closes the follow-up declared in 0.17.0.
- **`use_registry_credentials` opt-in (#77).** Consumers pulling private
  images declare it; only then does the dockerfile-mode build resolve
  registry credentials (and a broken helper is a hard, named error).

### Fixed

- **A stale credential helper no longer breaks dockerfile-mode builds
  (#77).** Device forensics: `credsStore: gcloud` left over in
  `~/.docker/config.json` after a gcloud uninstall hard-failed the build
  with `StoreError('docker-credential-gcloud not installed…')` — docker-py
  eagerly resolves credentials for ALL configured registries, where the
  docker CLI is tolerant (recorded error class: switching from CLI to SDK
  inherits different configuration behavior). The launcher builds local
  Dockerfiles from public base images and needs no registry login, so by
  default the resolution is not even started. Consumers that pull private
  images declare `use_registry_credentials: true` — only then do helpers
  run, and a broken one is a hard error naming the repair. Proxy settings
  from the user config still apply to builds (docker-py default) but are
  now announced in the log. User-config state (credsStore/credHelpers/
  proxies) is a new axis in `docs/environment-matrix.md`.

### Added

- **Rendered-port preflight (field finding 2026-07-28).** The compose
  capability gate now renders the file (`compose config --format json`,
  with `.env` interpolation applied) and compares the ACTUALLY published
  host ports with the launcher's port. A mismatch — the wiring class
  where `env_port_key` does not match the compose file's port variable,
  or a stray `.env` override wins — is a collected blocker naming both
  ports and the key, instead of a green install whose health check later
  probes the wrong port. Best-effort: frontends without `--format json`
  skip the check. The `.env` write moved before the gate so the render
  sees the build's real inputs.
- **`repo_url` misread named at the source (#74).** When the compose file
  is missing AND `repo_url` is set, the collected report now states that
  the launcher does not clone it and that `install_dir` must point at a
  local checkout.

## [0.21.1] - 2026-07-28

### Fixed

- **Example configs work with `--install` again (#74 context).** A relative
  `install_dir` in a file-loaded config now resolves against the config
  file's directory (same rationale as the #64 base rule), and the
  `test-configs/` examples point at the neighbor app checkouts. Previously
  the #64 base rule made them base on `test-configs/` itself, so the
  compose file was "not found" right next to a `repo_url` that names it —
  and `repo_url` is informational only (the launcher does not clone; #74
  carries that feature decision).

## [0.21.0] - 2026-07-25

### Added

- **Snap-confinement detection (#63).** When the launcher runs inside a Snap
  sandbox (`SNAP` / `SNAP_NAME` set), it logs a clear, documented warning at
  startup that paths outside the snap-writable area (a compose file or build
  context under the real home, `/mnt`, `/media`) can fail to read and that
  bind mounts to them can silently do nothing, instead of failing silently.
  New `docker_app_launcher.snap` module. Part of the environment matrix (#56).

- **Pre-build environment pre-flight (#61, #59).** Two checks now happen
  before a multi-minute build instead of failing deep inside it. A **disk
  pre-check** (`min_build_disk_bytes`, ~2 GB advisory default, 0 disables)
  flags clearly-insufficient free space on the build directory with an
  actionable message (#61). And because the app is offline-first but INSTALL
  needs the network to pull base images, install now **warns up front that
  internet is required** and **classifies a network/DNS build failure
  distinctly** from other build errors (#59). New i18n keys in all 11
  catalogs. Part of the environment matrix (#56).

- **Robust build-base resolution (#64).** App-relative paths (the compose
  file and the build context) no longer silently fall back to the current
  working directory when `install_dir` is unset. A file-loaded config now
  derives its base from the config file's own directory (`from_json`), and
  when no base can be determined the readiness gate says so loudly and advises
  setting `install_dir` (new `base_is_cwd_fallback`; new i18n keys, all 11
  catalogs) instead of building against the wrong directory under a frozen
  binary / Snap / desktop launch. Part of the environment matrix (#56).

- **Endpoint-aware Docker detection (#57, #62).** The "add yourself to the
  `docker` group" self-repair is now offered ONLY on the classic root unix
  socket, where group membership actually governs access. On rootless
  (`$XDG_RUNTIME_DIR/docker.sock` or `/run/user/<uid>`), a remote
  `DOCKER_HOST=tcp://`, or Docker Desktop's per-user socket it gave wrong
  advice; those cases now get endpoint-appropriate guidance instead (#57).
  The rootless socket is also probed as a detection fallback, so a rootless
  user without a configured `DOCKER_HOST` is found instead of reported as
  "not started" (#62). Part of the environment matrix (#56).
- **Build capability gate: readiness now proves capability, not existence
  (#54).** The compose path had an unwritten version chain (engine -> CLI ->
  compose plugin -> buildx) and the old ladder only proved the plugin
  EXISTED. On a Docker-20.10-era device the plugin was present, the check
  went green, and the build still failed minutes in with `compose build
  requires buildx 0.17 or later` (buildx 0.8.2). The launcher now runs one
  capability gate per mode BEFORE the build and COLLECTS every missing/too-old
  link into a single message: compose file present and readable, a usable
  compose frontend, and buildx at a sufficient version. Minimum backed by the
  docker/compose source (`getBuildxPlugin`, `buildxMinVersion = 0.17.0`) and
  applied only when compose is new enough to enforce it (>= 2.40.2, the
  release that first hard-gates buildx), so no build that would actually
  succeed is blocked. The buildx message names the distribution-independent
  fix (download the binary to `~/.docker/cli-plugins/docker-buildx`,
  `chmod +x`), because package sources proved unreliable.
- **App-declared Docker minimums.** New optional config fields
  `min_engine_version`, `min_api_version`, `min_compose_version`,
  `min_buildx_version` let a consumer app declare the environment its
  Dockerfile / compose file needs. Effective requirement = max(intrinsic,
  declared): the config can only RAISE the bar, never lower it (a value below
  the launcher's intrinsic floor is warned about and the intrinsic value
  wins). Messages attribute the source (app vs launcher). Dirty real-world
  version strings (`20.10.21+dfsg1`, `v0.8.2-docker`) are normalized and
  compared with `packaging`; an unparsable declared minimum is a hard error at
  startup. Backward compatible: unset fields mean only the intrinsic
  requirements apply.
- **Docker toolchain version line in the log (#54).** Engine, CLI, API,
  compose and buildx versions are read once and logged as a single line
  before the build, so a future failure report already carries the whole
  chain without the user running any commands.
- **A running build can be cancelled; closing the window terminates it
  (#60).** A multi-minute `docker build` used to keep running after the
  window closed, orphaning the subprocess. `install` / `start` (and the
  `ensure_installed` entry point) now accept an optional `should_cancel`
  predicate, polled on its own thread while the build streams; when it fires
  the build subprocess is killed and the action returns a clean, localized
  "build cancelled" result rather than a failure. The Tk window arms the
  signal on close-during-build, so the X button no longer leaves an orphaned
  build behind.
- **macOS/Windows install is now verified in CI (#58).** A new
  `cross-platform-smoke` job installs the package and smoke-runs it (`import`,
  `--version`, `--status`) on `macos-latest` and `windows-latest`. Until now
  no macOS or Windows path was built or started by any automation - the
  zero-cost PyPI/pipx distribution (which sidesteps both macOS Gatekeeper and
  Windows SmartScreen) was never even confirmed to install and start on those
  OSes. This is the decision-independent half of the distribution gap; the
  signed downloadable-binary decision (Apple notarization / Windows code
  signing) remains open in #58.


### Changed

- **Dockerfile mode preconditions are now collected too (#51/#54).** The
  dockerfile build path applies the same capability-not-existence gate:
  docker-py importable, Dockerfile present and readable, build context
  resolvable, plus any app-declared engine/API floor - reported together, not
  one failed run at a time. (No buildx gate: the classic docker-py builder
  does not use buildx.)

## [0.20.0] - 2026-07-25

### Added

- **Command transparency (#49).** Every external command is announced in
  the log BEFORE it runs (one shlex-quoted line, INFO; expected-to-fail
  status probes stay DEBUG) and its result carries the exit code. Failed
  operations report the FIRST meaningful stderr line + exit code + the
  exact command — a trailing help dump never becomes the error message
  (the full output stays in the log / App-logs button).
- **Dockerfile deployment mode (#51).** New `deployment_mode:
  "dockerfile"`: build and run a single-service app directly through the
  docker-py API — zero Compose dependency, so it runs on Docker-20.10-era
  systems without the compose plugin (the #48 device class). The
  mode-specific config block covers Dockerfile path, build context,
  published/container port, named volumes, environment and restart
  policy; a missing block detail (Dockerfile, docker-py) is a hard,
  actionable error. Build output streams live into the log panel; socket
  errors reuse the #44 exception classification. Default rule: existing
  configs keep the compose mode unchanged. The compose-missing error now
  names the dockerfile mode as the single-service alternative.
- **Compose availability ladder (#48).** The launcher now detects a usable
  Compose frontend BEFORE any build: `docker compose version` (plugin) →
  `docker-compose --version` (legacy v1, accepted only when it can parse
  the app's compose file via `config -q`) → neither: a hard, actionable
  error naming the missing piece and how to install it (Ubuntu:
  `docker-compose-plugin`). Verified device forensics: a Docker 20.10 CLI
  without the plugin swallows the word `compose` and dies on `-p` with
  `unknown shorthand flag: 'p'` plus the full help dump — reproduced
  character-identically against a real 20.10.24 CLI in a plugin-free
  container. The detected frontend is cached per process and every compose
  invocation (build/up/logs) is constructed through it, so legacy v1
  systems keep working via `docker-compose`.

## [0.19.0] - 2026-07-25

### Added

- **A second launch now focuses the running window (#31).** The refused
  second instance drops a focus marker next to the lockfile; the running
  window polls it (1s) and brings itself to the foreground
  (deiconify/lift/focus in Tk terms, showNormal/raise/activate in Qt) —
  instead of only printing "already running" while the user searches for
  the window. File-based on purpose: portable and unit-testable without a
  second process.
- **Keyboard accessibility (#31).** Entering a state puts keyboard focus
  on its primary action (install / start / open browser / about) — only on
  a real state change, never on polling refreshes, so the port field keeps
  its focus while typing. Tk buttons get an explicit focus ring; CTk
  buttons paint a focus border on FocusIn (they had no indicator at all);
  Qt keeps Fusion's native focus frame.

### Fixed

- **A missing explicit `--config` path is now a hard error (#32).** It
  used to silently launch an all-defaults "My App" window — the wrapper
  deployment bug class where only strace found the wrong bundled path.
  Exit code 2 with the path on stderr; the implicit `launcher.json`
  lookup stays fail-open.

## [0.18.0] - 2026-07-25

### Fixed

- **State text no longer clips at the window edge (#47).** The wordiest
  state message (docker_no_permission: detail + usermod command + re-login
  hint) rendered as over-wide lines with no word wrap in ANY frontend and
  was cut off on the device. tk/ctk now set a dynamic `wraplength` that
  follows the actual window width while resizing; Qt sets
  `setWordWrap(True)`. Verified with the longest catalog text: the label's
  required width stays within the window in every state.

### Changed

- **The window is resizable by default.** `window_resizable` now defaults
  to `True` — the log panel is the window's core and a fixed 620×520 clips
  it on small screens or with large fonts; the persisted geometry (#31)
  keeps whatever size the user settles on. The Qt frontend now honors the
  `window_resizable=False` opt-out too (`setFixedSize`, parity with
  tk/ctk — it previously ignored the flag).

## [0.17.0] - 2026-07-25

### Added

- **Hybrid docker-py adoption (#44).** Inspection now goes through the
  native Docker API (docker-py 7.2.0) with typed exceptions instead of
  scraping the CLI's unversioned stderr text — the #27 root cause class:
  - `check_docker`: `ping()` is authoritative for "running" and
    "permission denied" (real errnos from the exception chain); the CLI
    probe still owns not-installed detection and remains the full
    fallback when docker-py is absent.
  - The #25 context sweep probes endpoints through the API first.
  - Container queries (`get_state` hot path) list via the API with the
    same name-filter semantics; CLI fallback unchanged.
  - New `actions.stream_app_logs()`: live follow of all project
    container logs (per-container threads, name-prefixed lines) — the
    backend for a future live GUI tail; the "App logs" button keeps its
    one-shot tail. GUI follow-mode wiring is a follow-up.
  - The compose lifecycle (`up --build`, `down`, …) deliberately stays
    on the CLI: Compose v2 is a Go plugin docker-py cannot replace.

- **"App logs" button (P2).** New secondary button (all three frontends)
  that fetches the tail of the app's container logs via
  `docker compose logs --tail` — enabled while running AND stopped, since a
  crashed container's last lines are exactly what a bug report needs. Tail
  length configurable via `LauncherConfig.log_tail_lines` (default 200);
  new `actions.app_logs()` for CLI↔GUI parity; localized in all 11
  languages.
- **`--log-level` CLI flag (P3).** Overrides the config's `log_level` per
  run (`--debug` still wins over both).
- **Frozen-binary CI gate (#38).** A bug that only exists in the frozen
  PyInstaller artifact (missing i18n catalogs #34, placeholder branding,
  wrong version) is invisible to source-tree tests by nature. The new
  mandatory `frozen-binary` CI job builds the real binary from the rendered
  spec, opens its real window under xvfb, and asserts the rendered contract
  via the new `--render-probe` CLI flag (title incl. version, translated
  labels — never raw keys, full button set). Proven RED against a binary
  built without package data, GREEN on the current spec. The probe writes
  its JSON contract to a file (never through the stdout pipe): `xvfb-run`
  merges the client's stderr into stdout, which would interleave log lines
  into piped JSON.

### Changed

- **Split by responsibility (#42).** `actions.py` and `gui.py` are now
  thin facades; the code lives in `docker/` (`detection`, `lifecycle`,
  `inventory`, `cleanup`, `command_runner`) and `frontends/` (one window
  per file), with the shared behaviour tables in `ui_model`. Import paths
  and the public API are unchanged; the test suite is mirrored per module.

### Fixed

- **Swallowed messages (P0/P1).** A systematic audit of "the launcher eats
  its output" found and closed five gaps:
  - `launch()` (the wrapper-app API) never configured logging, so wrapper
    runs had NO handlers at all — it now calls `setup_logging()` (opt-out:
    `configure_logging=False`), which is also idempotent (no duplicated
    handlers/lines on repeated setup).
  - Failed docker subprocesses only ever logged at DEBUG; failures
    (non-zero exit, timeout, missing binary) now log at WARNING with the
    stderr tail — expected-to-fail status probes stay at DEBUG.
  - The GUI log panel was write-only into the widget; every panel line is
    now mirrored into `launcher.log` (`err` lines at ERROR).
  - Uncaught exceptions vanished to an invisible stderr: Tk callback
    exceptions are now logged AND shown in the panel
    (`report_callback_exception`), and process-wide `sys.excepthook` /
    `threading.excepthook` log uncaught crashes.
  - A crash inside a worker thread left the window stuck in its busy
    state; worker bodies are now guarded (`run_guarded`) so a crash
    becomes a normal failed result that re-enables the buttons.
  - The always-on stream log handler moved from stdout to stderr so
    machine-readable output (`--render-probe` JSON, `--status`) stays
    clean.

- **EACCES no longer misclassified as daemon-down (#27 reopened).** Device
  verification on the frozen v0.16.0 binary showed the daemon-down flow
  (systemctl hint + start button) although the daemon ran and only the
  docker-group membership was missing. Classification no longer depends on
  the docker CLI's unguaranteed error text: a direct connect on the active
  unix socket yields the truthful errno (EACCES/EPERM → permission,
  ECONNREFUSED/ENOENT → down) before any context sweep. New real-daemon
  integration tests (privileged container, both directions) cover the signal
  GENERATION — the previous simulation only ever validated signal
  processing.

## [0.16.0] - 2026-07-24

### Added

- **The About dialog reports the ACTUALLY RUNNING app version, clearly
  labelled (#36).** `actions.get_app_version()` resolves the app version
  with an explicit source label — `running` (probed from the live app's
  health endpoint via the new `app_version_health_key` config field),
  `installed` (install manifest), or `expected` (the static
  `app_version` the wrapper ships) — so the dialog can no longer claim a
  version nobody is running. New line format: `App: <name> <version>
  (<source>)` plus a separate `Launcher: docker-app-launcher vX.Y.Z` line.

- **Systematic rendering matrix (#37).** `tests/test_state_matrix.py` drives
  every frontend through every state in the central `BUTTON_STATES` table
  and inspects the rendered widget tree: title (product name + installed
  version, no placeholder), complete button set, label uniqueness across the
  whole window including transient buttons, exact enablement equality, and
  detection-log streaming. Proven against the old faulty commits: the
  missing-version and duplicate-label bugs fail the matrix in all three
  frontends. Frozen-binary CI verification tracked separately (#38).

### Fixed

- **Two identically labelled "cleanup" buttons (#33).** With the startup
  cleanup offer visible, the fixed scan button and the transient offer button
  both read "cleanup" — two different actions, one label. The offer button
  now says "Clean up now" (`cleanup_now`, all 11 languages) in all three
  frontends; the fixed scan button keeps its label.
- **Frozen binaries showed raw i18n keys (#34).** The PyInstaller spec
  template never bundled the package data, so every frozen build was missing
  all 11 translation catalogs and `i18n.t` fell back to the key names
  (`not_installed`, `log_copy`, …). The template now collects the package
  data files (`collect_data_files("docker_app_launcher")`).

### Added

- **Version visible everywhere (#30).** The window title now reads
  `My App — v0.15.0` (version read from the installed package metadata,
  never hardcoded), the first log line states version · backend · platform,
  and a new always-enabled "About…" button (even in the no-docker state —
  bug reports happen exactly there) shows version, platform, GUI backend,
  the active docker endpoint override, and offers to open the issue tracker.
- **Docker-detection steps stream into the visible log (#30).** The context
  sweep reports every probed endpoint ("Checking Docker context
  'desktop-linux' (…)…", all 11 languages) into the existing log area, so a
  multi-second detection is visible progress instead of a frozen message.
- **Uninstall asks first (#31).** Uninstall is destructive
  (containers + images); a confirmation dialog (existing `confirm_uninstall`
  catalogs, previously unused) now guards it in all three frontends. Stop
  stays unconfirmed on purpose: it is loss-free and instantly reversible.
- **The window reopens where you left it (#31).** Geometry is persisted to
  the launcher JSON on quit and restored on start (all three frontends).

## [0.15.0] - 2026-07-24

### Added

- **Self-repair for missing docker-group membership (Linux, #27).** The
  no-docker help panel (all three frontends) gains a "Set up Docker access…"
  button when a socket-permission error is detected: a confirmation dialog
  states honestly that docker-group membership effectively grants root
  privileges, then `pkexec usermod -aG docker $USER` runs and the result is
  VERIFIED against `getent group docker`. The success message still demands
  logging out and back in — it never claims Docker is usable already, because
  the group change only becomes active in a new login session. Dismissing the
  polkit dialog or a failure falls back to the manual instructions, never
  silently.
- **Waiting for Docker Desktop after starting it (#28).** After a successful
  daemon/Desktop start every frontend now polls (`actions.wait_for_docker`)
  with an indeterminate progress and a localized "Docker Desktop is
  starting…" note instead of instantly reporting "not started" again while
  the VM boots.

### Fixed

- **Permission-denied no longer reads as "Docker is not started" (#27).**
  `docker-app-launcher --check` (and every `check_docker` caller) now has a
  dedicated permission branch; previously a permission error fell through to
  the generic message, sending users to `systemctl start docker` although the
  daemon was already running. The localized `docker_no_permission` message
  (all 11 languages) now carries the complete fix: the `usermod` command AND
  the explicit log-out/log-in (or reboot) requirement, plus the
  `newgrp docker` terminal-only shortcut — running usermod alone changes
  nothing in the current session, which was exactly the confusion observed.

## [0.14.1] - 2026-07-23

### Fixed

- **Documentation caught up with 0.14.0.** README (EN + DE), CLAUDE.md and
  the architecture document now cover the swappable frontends: the
  `gui_backend` field with the tk/ctk/qt table, the `ctk`/`qt` install
  extras, the `ui_model` module, the third-party entry-point group, and the
  new make targets — the release's headline feature was invisible on the
  PyPI page.
- **GUI screenshots capture reliably.** pyautogui silently produced nothing
  under xvfb and on Wayland desktops, leaving stale bright screenshots in
  place; capture now falls back pyautogui → ImageMagick `import` → Pillow
  ImageGrab, a total miss surfaces as a pytest warning, and
  `make screenshots` clears `test-screenshots/` first.

## [0.14.0] - 2026-07-23

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
- **CustomTkinter frontend (`gui_backend: "ctk"`).** The first alternative
  frontend: the same window — state heading, port row with live validation,
  language picker, primary grid + secondary row, log, progress bar, cleanup
  offer, docker-help panel, tray/background behaviour — rendered with modern
  CustomTkinter widgets (dark/light follows the OS). New optional extra
  `ctk` (`pip install docker-app-launcher[ctk]`); without it the frontend
  refuses with an install hint. 17 real-window tests assert the SAME
  `ui_model` behaviour as the Tk frontend.
- **PySide6 (Qt) frontend (`gui_backend: "qt"`).** The second reference
  frontend, on a genuinely different toolkit: worker threads marshal onto
  the GUI thread via a queued Qt signal instead of Tk's `after`, closing is
  a `closeEvent`, tooltips/clipboard/progress are Qt-native — while every
  decision still comes from the shared `ui_model`, including the pystray
  background behaviour through a small `withdraw`/`iconify` adapter. New
  optional extra `qt` (`pip install docker-app-launcher[qt]`; PySide6 caps
  its Python range at <3.15, hence a marker). Its 20 tests run on Qt's
  `offscreen` platform — no display, no xvfb — so they run on any bare box;
  screenshots come from Qt's native `grab()`.

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

[Unreleased]: https://github.com/astrapi69/docker-app-launcher/compare/v0.23.0...HEAD
[0.23.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.21.1...v0.22.0
[0.21.1]: https://github.com/astrapi69/docker-app-launcher/compare/v0.21.0...v0.21.1
[0.21.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.14.1...v0.15.0
[0.14.1]: https://github.com/astrapi69/docker-app-launcher/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/astrapi69/docker-app-launcher/compare/v0.13.0...v0.14.0
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
