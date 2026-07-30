# Environment matrix - exploration

Status: exploration (document first, no code fixes in this document's scope
except the test matrix in Part 2). Owner: environment-robustness track.
Method: verify-first. Code claims are cited as `file:line` against `main`;
external facts are cited to primary sources (Docker docs, Apple developer
news, PyInstaller docs) in the Sources appendix.

## Why this exists

Four environment defects in a row were all discovered on a single QA device,
one after another, each only after the previous was fixed: the compose file
was not bundled, the compose plugin was missing, the install hint named a
non-existent package, buildx was too old (#54). All four were predictable.
None were predicted. The cause is not carelessness in the fixes; it is a
missing foundation. There was no document naming the target environments and
no test matrix checking against them. This document is that foundation. The
recorded error class (see `CLAUDE.md`, "Readiness Rules and Error Classes"):
**environment assumptions are checked against a documented matrix, not
discovered one device at a time.**

The best-tested environment cell today is "Linux, root daemon, `/var/run/docker.sock`,
user in the docker group." Almost everything else (rootless, remote/tcp,
Docker Desktop context, macOS, Windows, Snap-confined, frozen binary with an
odd working directory) is asserted only through mocked unit tests.

---

## Part 1 - exploration

### 1.1 Target environments (the variants that actually occur)

#### Operating systems

- Linux: Ubuntu LTS (22.04, 24.04) and Debian (11, 12) are the primary
  targets; other distros exist but track the same Docker sources.
- macOS: Intel (x86_64) and Apple Silicon (arm64) - the arch split is
  load-bearing for binary distribution (see 1.4).
- Windows 10/11.

#### Docker install sources and the generations they ship

The recurring launcher-breaker: distro-packaged Docker is old, ships no
Compose v2 plugin, and ships no or ancient buildx. The "20.10 + buildx 0.8.2
+ freshly-added compose plugin" QA device is the textbook distro profile, not
an outlier.

| Source | Engine (old end) | API | Compose | buildx | Notes |
|---|---|---|---|---|---|
| `apt install docker.io` (Ubuntu 22.04) | 20.10.x at release (ports arch still `20.10.12`) | ~1.41 | none in pkg; `docker-compose` v1 (Python, ~1.29.2, EOL) separately | none / very old | compose and buildx are separate packages the user must know to add |
| `apt install docker.io` (Ubuntu 24.04) | 24.0.7 (ports arch) | ~1.43 | none in pkg; `docker-compose-v2` separate | separate `docker-buildx` pkg | amd64 gets security-bumped, ports architectures stay frozen at release |
| `apt install docker.io` (Debian 11/12) | 20.10.5+dfsg1 / 20.10.24+dfsg1 | ~1.41 | v1 or none | absent in `docker.io` | note the dirty `+dfsg1` suffix - version strings must be normalized |
| Docker apt repo `docker-ce` | 27.x / 28.x | ~1.47 / ~1.48 | v2 plugin, 2.2x+ | 0.17+ | the "everything works" baseline; the intrinsic buildx-0.17 gate passes here and essentially only here out of the box |
| `snap install docker` | 24.x - 28.x | current | bundled | bundled | version is fine; confinement is the problem (see 1.2) |
| Docker Desktop (Linux/macOS/Win) | 27.x / 28.x | current | v2 | current | installs a `desktop-linux` / `desktop-*` context whose endpoint is `~/.docker/desktop/docker.sock`, NOT `/var/run/docker.sock` |
| Rootless Docker | current | current | user-scoped | user-scoped | socket at `$XDG_RUNTIME_DIR/docker.sock`; `usermod -aG docker` is irrelevant here |
| Podman + docker-compat socket | n/a (Podman) | partial | `podman-compose` (BuildKit force-disabled) | NOT supported | `DOCKER_HOST=unix://.../podman.sock`; buildx/BuildKit unsupported - see non-support in 1.5 |

Consequence: the launcher must treat "engine present" and "compose/buildx
present" as independent axes, each of which can be absent or too old. The
#54 capability gate (in flight, PR #55) is the first piece to model this for
the compose build path; `main` today has the #48 compose-availability ladder
but no buildx-version awareness.

Image mode (#78) deliberately collapses this table: it needs only a reachable
engine API (`/images/create` + the containers API), so every row above -
including the frozen-at-release distro packages with no compose and no
buildx - is a supported cell for `deployment_mode: "image"`. That is the
end-user distribution answer to the toolchain matrix, not a workaround for
it: the build rows still apply unchanged to compose/dockerfile mode.

#### Access paths (how the launcher reaches the daemon)

- Unix socket as a docker-group member (the classic, best-tested case).
- Unix socket WITHOUT group membership -> EACCES (permission), handled by
  the #27 errno probe.
- Rootless socket under `$XDG_RUNTIME_DIR/docker.sock`.
- `DOCKER_HOST` set (tcp:// remote, or a unix path).
- Active `docker context` != `default` (Docker Desktop's `desktop-linux`, a
  rootless context, etc.).
- Windows named pipe `npipe://`.

#### Client-configuration state (`~/.docker/config.json` on used machines)

A real axis on second-hand/developer machines - the config outlives the
tools that wrote it:

- `credsStore` set to a helper that IS installed (desktop, pass, secretservice).
- `credsStore` set to a helper that is GONE (verified field case:
  `docker-credential-gcloud` leftover after a gcloud uninstall - the CLI
  tolerates it, docker-py hard-fails the build with `StoreError`, #77).
- Per-registry `credHelpers` entries, present and dangling.
- `proxies` - docker-py injects them into builds as build args
  (`use_config_proxy=True` default); the launcher keeps that default but
  LOGS the variable NAMES (never values). A credentialed proxy URL
  (`http://user:pass@proxy`) triggers a masked WARNING: with the classic
  builder, build args land in the image history. Masking before the build
  would break authenticated proxies - pass-through + warning is the
  deliberate, tested choice (password proven absent from every log line).
- Plugin references (`cliPluginsExtraDirs`) and a non-default active
  context (covered by the access-path axis, listed here for completeness).

Launcher stance (#77): dockerfile-mode builds do NOT resolve registry
credentials by default (public base images need none); a consumer that
pulls private images declares `use_registry_credentials: true` - only
then is a broken helper a hard, named error.

### 1.2 Launcher assumptions checked against the matrix

Findings from a full audit of `main` (each cited). "OK" means the code is
already robust for that axis.

**Socket / endpoint selection.**
- OK: `DOCKER_HOST` is honored implicitly - the launcher relies on
  `docker.from_env()` (`docker/py_client.py:49`) and the `docker` CLI
  inheriting the process env, rather than hard-coding an endpoint.
- OK: the only hard-coded socket path is a diagnostic fallback default,
  `os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")`
  (`docker/detection.py:136`) - used for messages/errno-probe, not to choose
  the connection.
- OK: a non-default active context is read from `docker context ls`
  (`docker/detection.py:131-136`) and non-active contexts are swept and, on a
  hit, pinned via a `DOCKER_HOST` override (`docker/detection.py:110-119`).
- GAP (G2): the rootless socket `$XDG_RUNTIME_DIR/docker.sock` is never
  explicitly probed. It is reached only if it is the active/other context;
  there is no default rootless probe. No `XDG_RUNTIME_DIR` reference exists in
  `src/`.
- Bounded: `npipe://` / `tcp://` skip the truthful-errno unix probe
  (`docker/detection.py:150-151`), so the permission-vs-down distinction is
  unix-only. Acceptable, documented.

**Permission self-repair (`usermod` / `pkexec`).**
- GAP (G1, highest-impact): the "add yourself to the docker group" offer is
  gated only by `platform.system() == "Linux"` plus a `permission` verdict
  (`docker/detection.py:277-280`), NOT by whether the endpoint is a local
  root unix socket. `add_user_to_docker_group` re-checks only the platform
  (`docker/detection.py:408`). It therefore gives actively WRONG advice on:
  - Rootless Docker (no privileged daemon; the docker group grants nothing).
  - Remote `DOCKER_HOST=tcp://` (permission is remote/TLS, not local group) -
    partially mitigated because the unix errno probe is skipped for tcp, but a
    docker-py EACCES still classifies as `permission` (`docker/py_client.py:90-93`).
  - Docker Desktop on Linux (per-user `desktop-linux` context socket; group
    membership does not apply).
  macOS/Windows are correctly excluded (the branch lives in the Linux block;
  `docker/detection.py:321-323`).

**Snap / confinement / frozen-binary paths.**
- GAP (G3, most fragile path assumption): the compose file, `.env`, and build
  context resolve relative to `install_dir` or fall back to `Path.cwd()`
  (`config.py:265, 315`; `launcher_settings.py:109`). A frozen binary launched
  from a `.desktop` file, a Snap, or a file-manager double-click has an
  unpredictable or read-only CWD, so an unset `install_dir` silently points
  compose at the wrong directory and silently no-ops the `.env` write (write
  failure is logged and swallowed).
- GAP (G7): no Snap awareness. Under strict confinement `HOME` is remapped to
  `$SNAP_USER_DATA`, compose files in hidden `$HOME` subdirs fail with
  permission denied, and bind mounts outside allowed locations silently
  succeed-but-do-nothing (canonical/docker-snap #189, #334). `config_dir`
  defaults to `Path.home()/.<slug>` (`config.py:241`) with no
  `SNAP`/`XDG_CONFIG_HOME` handling.
- OK: writable log sinks are best-effort and guarded, degrading to "fewer
  sinks" rather than crashing (`logging_setup.py:127-128, 137-138, 150-151`);
  the `--debug` sink at `Path.cwd()/launcher-debug.log` (`logging_setup.py:144`)
  is the only CWD-dependent write and it is non-fatal.
- OK: frozen version discovery bakes `__build_version__` into the binary
  (`pyinstaller/build_info.py`) and the spec bundles package data incl. the
  i18n catalogs (`launcher.spec.template` `collect_data_files`), fixing #34.

**CLI behavior that varies by generation.**
- OK: the compose frontend ladder (`docker/compose_runtime.py:56-68`) is the
  only generation-aware detection and it does not assume compose v2.
- Bounded: BuildKit progress parsing assumes `#<n> [stage x/y]` output
  (`docker/command_runner.py:69`); on a legacy non-BuildKit builder the bar
  simply never advances (graceful).
- Bounded: `docker context ls --format` (`docker/detection.py:79-81`) did not
  exist on very old CLIs; any failure degrades to `[]` (`docker/detection.py:83-86`).
- Note: `main` uses no `docker buildx` and no `docker version --format`; the
  #54 branch adds both behind the readiness gate.

### 1.3 Error classes beyond detection

Each with today's behavior on `main`.

| Class | Today's behavior | Verdict |
|---|---|---|
| Disk full during image build | No pre-check; the build fails deep in with Docker's raw ENOSPC in the tail. | GAP (G4): no up-front free-space check, no dedicated message. |
| No internet on first build (base image cannot be pulled) | No pre-check, no pre-warning. The app is offline-first, so users reasonably assume install is offline too - but install needs the network to pull base images. | GAP (G5): install must pre-warn "installation needs internet" and classify a pull failure distinctly. |
| Long compose build with no feedback | Build output streams line-by-line into the log panel and a parsed progress bar advances (`command_runner.py` BuildKit parsing). | OK on BuildKit; on a legacy builder the bar stalls (see 1.2). |
| Cancel a running build / close the window mid-build | No cancel control. `_on_close` only routes to tray/quit (`frontends/tk_window.py:796-810`); the build runs on a daemon thread and its subprocess is not explicitly terminated. | GAP (G6): add a cancel action and terminate the build process group on close. |
| Port occupied by a foreign process | `install` guards with `check_port` and returns a localized `port_occupied` message before building. | OK (cannot distinguish "our old container" from a foreign process, minor). |
| Upgrade an existing install to a new app version | `start` does `up --build -d` picking up changes; volumes persist across the rebuild. | OK: data survives. |
| Uninstall | Containers and images are removed; VOLUMES ARE PRESERVED (`docker/lifecycle.py:640`), and cleanup unconditionally protects active-project volumes (#11, `docker/cleanup.py:82-105`). #30 added a destructive-action confirmation. | OK: data-safe; the message could state more explicitly that data volumes are kept. |

### 1.4 Distribution and trust (high priority - decision template, no decision here)

Critical finding first: **no macOS or Windows binary is built or started by
any of this project's automation.** `publish.yml` ships only a PyPI
sdist/wheel on `ubuntu-latest` (`.github/workflows/publish.yml`); the only
binary build+launch is the Linux frozen-binary CI job under xvfb
(`.github/workflows/ci.yml`, the `frozen-binary` job). There is no
`macos-latest` or `windows-latest` anywhere. Every macOS/Windows code path
(`open /Applications/Docker.app`, `%ProgramFiles%\Docker\...`, `npipe://`,
`SO_EXCLUSIVEADDRUSE`, `CREATE_NO_WINDOW`) is exercised only by mocked unit
tests, never on a real host or binary.

Therefore, answering the mandated question directly: **do the macOS and
Windows binaries start? Unverified - and unverifiable from this repo,
because nothing builds or launches them.** If such binaries are being
distributed, they are produced outside this repo's CI and have never been
start-tested here. This is the top distribution gap (G9).

What WOULD happen to an unsigned, un-notarized PyInstaller onefile:

- macOS Apple Silicon (arm64), does it even start? An unsigned arm64 Mach-O
  is `SIGKILL`ed by the kernel ("Killed: 9") regardless of Gatekeeper -
  Apple silicon has required at least an ad-hoc signature since macOS 11.
  A stock `pyinstaller --onefile` build starts ONLY because PyInstaller
  ad-hoc re-signs by default (its binary processing invalidates signatures,
  so it regenerates them). Stripping/UPX/post-processing that invalidates the
  ad-hoc signature reintroduces `Killed: 9`. Intel Macs do not require
  signing to launch.
- macOS Gatekeeper (Sequoia / macOS 15): a downloaded (quarantined) unsigned
  binary shows "Apple could not verify ... is free of malware," and macOS 15
  REMOVED the Control-click -> Open bypass (Apple developer news, Aug 2024);
  the user must go to System Settings -> Privacy & Security -> "Open Anyway"
  (admin auth), or strip quarantine with `xattr -dr com.apple.quarantine`.
  Ad-hoc signing does NOT satisfy Gatekeeper for a quarantined download.
- Windows SmartScreen: an unsigned exe with the Mark of the Web and no
  reputation triggers "Windows protected your PC" -> More info -> Run anyway.
  Since March 2024 EV certificates no longer grant an instant SmartScreen
  bypass; EV and OV both accrue reputation over time.
- Antivirus false positives: a structural PyInstaller onefile problem (the
  self-extracting bootloader trips heuristics; much Windows malware is packed
  the same way). Common enough that PyInstaller documents it.

Options (effort / cost / what it fixes) - a decision template, not a pick:

| Option | Cost | Fixes | Does not fix |
|---|---|---|---|
| Apple Developer Program (Developer ID sign + notarize; needs a macOS CI runner) | $99/yr | Removes the macOS Gatekeeper wall incl. Sequoia | Windows |
| Windows OV cert (`signtool`; HSM/token mandatory since Jun 2023) | ~$200-400/yr | Establishes identity; reputation accrues over time | No instant SmartScreen clearance |
| Windows EV cert | ~$250-600/yr | Identity + faster trust | Post-Mar-2024, no instant SmartScreen bypass |
| Ad-hoc sign on Apple Silicon (`codesign -s -`; PyInstaller does it automatically) | Free | Makes the arm64 binary run at all | Does not satisfy Gatekeeper for downloads |
| Distribute via PyPI (`pipx install docker-app-launcher`) | Free | Sidesteps BOTH Gatekeeper and SmartScreen (no downloaded binary, no MOTW/quarantine) | Requires a Python on the user's machine; not a double-click artifact |
| Document the manual bypass | Free | Lets determined users run the unsigned binary | Poor UX; blocked where policy forbids unsigned code |

Honest framing for the decision-maker: the project already publishes to
PyPI, so `pipx install` is the one path that avoids both OS trust systems at
zero cost and zero annual maintenance. The signed-binary options are additive
polish for users who insist on a downloadable executable. On macOS the
unavoidable minimum for arm64 is ad-hoc signing just to launch (PyInstaller
already does this), and only Apple notarization ($99/yr) removes the download
warning. The immediate, cost-free action independent of the signing decision:
actually build and start-verify the macOS/Windows binaries in CI (or stop
advertising them).

Status: the decision-independent half is now in CI. The `cross-platform-smoke`
job (`.github/workflows/ci.yml`) installs the package and smoke-runs it
(`import`, `--version`, `--status`) on `macos-latest` and `windows-latest`, so
the zero-cost PyPI/pipx path is continuously verified to install and start on
both OSes. The signed downloadable-binary options in the table above remain a
maintainer decision (still open in #58).

#### Distribution decision (closed 2026-07-29, #73)

PyPI/pipx is the supported install path on macOS/Windows (CI-verified,
#71). No signing/notarization investment: direct downloadable binaries
stay best-effort with the documented manual-approval steps above.

### 1.5 Prioritization and explicit non-support

Ranked by frequency in the target audience x severity. The collector issue is
#56; each gap has its own issue.

1. G1 (#57) - permission self-repair offered on wrong endpoints (rootless /
   remote tcp / Desktop-linux). Severity high (actively wrong advice that
   misleads diagnosis); frequency rising (rootless and Desktop are common).
   Fix: gate the `usermod` offer to a local root unix socket only.
2. G3 (#64) - `Path.cwd()` as the compose/`.env`/build-context base when
   `install_dir` is unset. Severity high (silent wrong-dir / silent port
   no-op); frequency high (desktop-launch, Snap, frozen are the normal launch
   methods). Fix: resolve a robust base and fail loudly when it cannot be
   determined.
3. G9 (#58) - macOS/Windows binaries are neither built nor start-verified by
   automation. Severity high for distribution trust; blocks any honest binary
   distribution. Action: build + smoke-launch in CI, then the 1.4 signing
   decision.
4. G5 (#59) - install has no "needs internet" pre-warning and no distinct
   base-image-pull-failure classification. Severity high for an offline-first
   app (users expect offline install).
5. G6 (#60) - no build cancel; close-during-build orphans the build
   subprocess. Severity medium; frequency medium.
6. G4 (#61) - no disk-space pre-check before a multi-minute build. Severity
   medium.
7. G2 (#62) - rootless socket not auto-probed. Severity medium; frequency
   rising.
8. G7 (#63) - Snap confinement unsupported/undocumented. Severity medium.

Already tracked, not re-opened:
- G8 - buildx generation gap: #54 (PR #55, in flight).

Explicitly NOT supported (documented boundary, not an accidental gap):
- Podman with the docker-compat socket. Its build path is exactly
  buildx/BuildKit, which Podman's socket does not support; the collect-all
  readiness gate cannot make an honest "this will build" promise there. The
  launcher may detect it and say so, but will not claim support.
- macOS/Windows as first-class targets until the 1.4 distribution decision is
  made; today they are best-effort and unverified.

---

## Part 2 - test matrix (implementation)

Principle: for the cells declared supported, build automated environment
tests as far as they are containerizable, reusing the existing container
harness. Anything not containerizable is an explicit manual checklist with a
named owner, never silently omitted.

### Automated (containerizable), reusing the #27 harness

The existing `tests/integration/run_docker_signal_integration.sh` pattern -
a `--privileged ubuntu:24.04` container running a real nested `dockerd`, the
package installed into a venv, pytest run as a dedicated user - is the
template. `tests/integration/run_env_matrix_integration.sh` +
`tests/integration/test_env_matrix_real.py` extend it to the environment
cells this audit flagged as previously mock-only. Each test is gated by
`DAL_ENV_MATRIX_INTEGRATION=1` (so it never runs in the Docker-free suite)
and by `DAL_ENV_MATRIX_SCENARIO=<cell>` (one container per cell). Like the
existing integration scripts, this is a manual/opt-in runner, not part of the
standard `make ci` job (it needs a real daemon and `--privileged`).

- NEW cell (#77): a `DOCKER_CONFIG` pointing at a broken `credsStore` -
  the dockerfile-mode build must succeed WITHOUT touching the helper
  (default) and must hard-fail with the named-helper message when
  `use_registry_credentials` is set. Deterministic unit coverage lives in
  `tests/docker/test_dockerfile_backend.py::TestRegistryAuthNeutralized`;
  the real-daemon proof is the same scenario with an actual build.

Seed cells shipped now:

| Cell | Provisioning | Asserts |
|---|---|---|
| `no_compose` | `docker.io` (no compose plugin, no v1), daemon up, run as root | the #48 ladder returns an actionable install hint before any build; never the raw `unknown shorthand flag` help dump |
| `no_docker` | no docker binary | `check_docker` reports not-installed with an install command |
| `no_group` | `docker.io`, daemon up, user NOT in the docker group | the ONE cell where `usermod -aG docker` is correct advice (a local root unix socket) |

- MEASURED cell (#78, #84): image mode on an old-generation engine. A
  PINNED `docker:20.10.24-dind` (API 1.41) daemon, provisioned to the
  distro profile - the runner PROVES no compose plugin, no legacy
  docker-compose, no buildx before any test, and fails otherwise (the
  dind convenience image bundles the client plugins; the cell removes
  them because the modeled `docker.io` profile has none) - pulls AND
  archive-loads a prebuilt image through `image_backend.up`, starts it,
  and answers HTTP. Runs in CI on every push (`image-mode-old-engine`
  job); local: `tests/integration/run_image_mode_old_engine_integration.sh`.
  First measured 2026-07-29: 3/3 green on 20.10.24. Extended the same day
  (#87) with GHCR: its anonymous token flow differs from Docker Hub's, so
  the cell also pulls `ghcr.io/traefik/whoami:v1.10` credential-free
  (auth neutralized by default per #77, no stored logins in the dind) and
  proves the refusal case - a denied/unknown GHCR repository yields a
  classified message naming the registry access and the
  `use_registry_credentials` path for private images, never a raw
  library error. 5/5 green on 20.10.24. One-time run against the real
  consumer reference after its first GHCR publish is tracked separately. This closes the
  old-generation promise with a measurement instead of the API-surface
  argument; a future failure here is a finding about the documented
  minimum engine generation, never a reason to bend the test.

- LIFECYCLE MATRIX (#79): the full operation set per deployment mode -
  install, install-when-installed, logs, stop, restart of the STOPPED
  stack, uninstall, stop-when-nothing-runs - against a real engine, for
  image, dockerfile and compose mode
  (`tests/integration/test_lifecycle_matrix_real.py`, runner
  `run_lifecycle_matrix_integration.sh`). The checked operation set is
  enumerated in the test and asserted complete per mode. **Runtime
  split:** the per-push `ci.yml` runs the mocked suite plus the fast
  old-engine cell (#84); the FULL lifecycle matrix runs nightly and on
  demand (`lifecycle-matrix.yml`) - a green push does NOT imply
  full-matrix coverage. First full run 2026-07-29: 3/3 modes green
  (~60 s local). Why mocked coverage is not enough here, twice proven:
  the #77 sentinel was correct for the build path and broke the pull
  path; the #78 dispatch fell from a successful image acquire into the
  compose build.

- UPDATE PATH (#88, #92): measured per mode inside the same lifecycle
  matrix, two complementary ways. The ONE-ACTION `update()` (#92) is an
  operation in `_drive_full_operation_set`: from the RUNNING state - exactly
  what `start()` refuses (`already_running`) - it stops, re-acquires
  (re-pull / rebuild), starts, and health-checks in one call, asserting the
  stack is running and healthy afterwards per mode. The MANUAL path (stop ->
  new reference -> start, #88, `TestUpdatePath*`) additionally proves the
  named volume survives and, in image mode, that the previous image remains
  for rollback. The rollback hint on a failed health check is covered by the
  mocked unit suite (`tests/docker/test_update.py`), since the matrix
  exercises the healthy path. Note: the one-step `update()` was deliberately
  folded into the existing per-mode driver rather than added as three more
  standalone install+build cells - the extra real-container churn made the
  single-daemon runner flaky (stop-not-verified / compose recreate races).

- COMPOSE RECREATE FINDING (2026-07-30, measured twice): a SUCCESSFUL
  BUILD IS NO PROOF AN UPDATE HAPPENED. Whether `docker compose up
  --build -d` recreated the container onto the rebuilt image depended on
  the user's Compose generation: the CI runner's Compose restarted the
  OLD container (rebuilt image sat unused; upstream docker/compose#9308 -
  the image identity is not part of the recreate decision), while a
  current local Compose (v5.1.4) recreated correctly. User-visible
  before the fix: the launcher reported success, the health endpoint
  answered from the old container, and the app showed its OLD version.
  Since the `--force-recreate` fix the launcher's start/update replace
  the container deterministically on EVERY Compose generation.
  MEASURED side effect of that fix (2026-07-30, real daemon): a file
  written inside the container OUTSIDE any named volume is gone after
  start(), the named-volume file survives, and the updated content is
  served - in-container writes outside named volumes are ephemeral per
  start. Checked against the reference consumer (adaptive-learner): all
  persistence is anchored to its data-dir named volume by design (DB,
  uploads, config overlays), no file logging outside - NO finding, the
  behavior change is safe there and documented in
  docs/consumer-integration.md as a consumer rule.

Cells enumerated for follow-up (need the corresponding gap fix or heavier
provisioning, tracked by their gap issue):

- `legacy_v1_incompatible` (docker-compose v1 + a v2-only compose file ->
  `legacy_incompatible`).
- `rootless` (rootless dockerd; assert detection does NOT offer `usermod`) -
  needs G1's endpoint-kind gate to assert the corrected behavior.
- `remote_tcp` (second daemon on tcp + `DOCKER_HOST`; same G1 assertion).
- `no_network_build` and `no_disk_build` (simulated; assert the G5 / G4
  pre-checks once they exist).

### Manual checklist (not containerizable)

Owner: a maintainer with the hardware; run per release or when the relevant
code changes.

- [ ] macOS Intel: download the published binary, confirm it launches past
      Gatekeeper (Open Anyway), reaches the no-docker screen.
- [ ] macOS Apple Silicon: confirm the binary is ad-hoc signed and launches
      (not `Killed: 9`); confirm the Sequoia Privacy & Security "Open Anyway"
      flow.
- [ ] Windows 10/11: download the exe, confirm SmartScreen "Run anyway" path,
      confirm no antivirus quarantine, confirm the window renders.
- [ ] Docker Desktop (mac/win/linux): active `desktop-*` context is honored;
      the launcher connects without assuming `/var/run/docker.sock`.
- [ ] Real docker-group re-login on Linux: after `usermod` + logout/login the
      launcher transitions from permission to running (the injected-signal
      unit test only validates processing, not the real re-login).
- [ ] Rootless Docker: confirm the launcher connects via
      `$XDG_RUNTIME_DIR/docker.sock` and does NOT offer `usermod`.
- [ ] Snap Docker: confirm behavior with a compose file under `~/.<app>/`
      (expected to fail today; tracked by G7).
- [ ] First launch shows NO "concurrency guard cannot work" note (#103):
      the marker writes to config_dir (home-anchored, same directory as
      launcher.json/manifest) - a note here would mean the wrapper
      redirected config_dir somewhere unwritable.
- [ ] FINAL ACCEPTANCE for the image-mode end-user path (#86/#81): on the
      REAL QA old device (the one the original field failures came from),
      run `--doctor` -> `--install` (image mode) -> `--health` ->
      `--support-bundle`, then the same via the GUI (system check +
      install). The credential-helper case in particular must come out as
      a classified, explained problem - CI cells are the daily currency,
      the device finding is the acceptance currency.

---

## Consumer note (not implemented here; belongs in the wrapper repo)

Changing the launcher's host port changes the app's browser origin
(`http://localhost:<port>`). An app that stores data offline in IndexedDB is
origin-bound, so a port change makes the app appear empty and can look like
lost data (e.g. lost learning progress). This is an environment risk to
record here and to handle as a dedicated issue in the consumer/wrapper repo
(warn before a port change, or migrate/relax the origin binding).

---

## Sources

Docker generations: packages.ubuntu.com/docker.io; golinuxcloud and
linuxcapable Docker-install guides; Docker contexts docs
(docs.docker.com/engine/manage-resources/contexts); Docker rootless docs
(docs.docker.com/engine/security/rootless); canonical/docker-snap issues #189
and #334; Podman docker-compat docs and containers/podman #17836.

Distribution/trust: Apple developer news "Updates to runtime protection in
macOS Sequoia" (developer.apple.com/news/?id=saqachfa); Apple Platform
Security (ad-hoc sufficiency on Apple silicon); PyInstaller feature-notes
(pyinstaller.org/en/stable/feature-notes.html) and PR #5581; Microsoft Learn
code-signing options; SSL.com OV/EV FAQ; PyInstaller AV-false-positive FAQ
(pythonguis.com) and pyinstaller #6754; Apple Developer Program pricing
(developer.apple.com/support/compare-memberships); pipx docs.

Code claims: audited against `main`; see the inline `file:line` citations.
