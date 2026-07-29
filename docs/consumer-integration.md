# Consumer integration guide

This guide is for **authors who want to ship their own app** behind
docker-app-launcher. It shows how to write a `launcher.json`, what the health
endpoint contract is, how ports, volumes and environment variables are wired,
how to pin versions, what to publish as release artifacts, and how updates
work.

For the full field reference see the [README](../README.md); this page focuses
on the decisions you make when you integrate an app.

---

## Pick a deployment mode

The launcher supports three modes via `deployment_mode`. Choose by asking who
runs your app and what they have on their machine.

| Mode | Who it is for | What the user machine needs |
|------|---------------|-----------------------------|
| `image` | **End users** (recommended). You publish a prebuilt image; nothing is built on the user's machine. | A Docker engine only. No Compose, no buildx, so it works on old Docker generations. |
| `dockerfile` | Installing from a source checkout: developers and anyone who wants a local build. | Docker engine plus a Dockerfile in the checkout. |
| `compose` | Real multi-service stacks (separate containers, Compose networking, `depends_on`). | Docker engine plus a usable Compose frontend. |

**Recommendation for shipping to end users: use `image` mode.** It has the
smallest requirement on the user's machine and the fastest install, because
there is no build step.

### `image` mode (the end-user path)

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
  "restart_policy": "unless-stopped",
  "health_check_path": "/api/health",
  "health_check_key": "status",
  "health_check_value": "ok",
  "app_version": "1.2.3",
  "repo_url": "https://github.com/owner/my-app"
}
```

- `image_reference` (required) is the address of your published image, either a
  tag or a digest. A missing `image_reference` is a hard error at config load.
- `image_archive` (optional) is a `docker save` archive shipped alongside the
  config. **When the file exists it wins:** the image is loaded from it and the
  registry is never contacted. See [Release artifacts](#release-artifacts).

### `dockerfile` mode (build from source, single service)

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
  "restart_policy": "unless-stopped",
  "health_check_path": "/health"
}
```

- `build_context` is relative to `install_dir`; `dockerfile_file` is relative to
  the build context.
- A missing Dockerfile or an unknown `deployment_mode` is a hard, actionable
  error. The launcher never guesses.

### `compose` mode (multi-service stacks, the default)

```json
{
  "app_name": "My Stack",
  "deployment_mode": "compose",
  "install_dir": "/opt/my-stack",
  "compose_file": "docker-compose.prod.yml",
  "default_port": 8080,
  "env_port_key": "APP_PORT",
  "health_check_path": "/api/health"
}
```

- The launcher detects a usable Compose frontend before any build: the Compose
  v2 plugin (`docker compose`), or legacy `docker-compose` v1 when it can parse
  your compose file.
- Use compose mode when your app is genuinely several containers. A single
  service is simpler and more portable in `image` or `dockerfile` mode.

---

## The health endpoint contract

The launcher decides whether your app is "ready" by calling an HTTP endpoint on
`http://localhost:<port><health_check_path>` and applying this rule:

- **`health_check_path`** is the path to probe (default `/`). Point it at a
  cheap, dependency-free endpoint your app can answer as soon as it is ready.
- **`health_check_key` empty (or unset): any HTTP 200 response counts as
  healthy.** This is the simplest contract. If your app returns 200 only when it
  is truly ready, you need nothing more.
- **`health_check_key` set:** the launcher additionally parses the 200 response
  as JSON and requires that the JSON field named by `health_check_key` equals
  `health_check_value`. For example `health_check_key: "status"` and
  `health_check_value: "ok"` require a body like `{"status": "ok"}`.

A response of HTTP 500-599 is reported as a server error; any other non-200 is
reported with its status code; an unreachable port is reported as not ready yet.
The launcher polls until the app is healthy or `health_check_timeout` seconds
elapse.

Optionally, set `app_version_health_key` to the JSON field that carries your
app's own version string. When present, the launcher reads the running version
straight from the app, which is the most reliable source (it survives
out-of-band rebuilds).

---

## Ports, volumes and environment

These behave the same in `image` and `dockerfile` mode:

- **Host port:** `default_port` is the port the user reaches in the browser. The
  user can change it in the GUI or with `--port`. In compose mode the port is
  wired through `env_port_key` into the `.env` file the compose file reads.
- **Container port:** `container_port` is the port your app listens on *inside*
  the container. The launcher maps the host port onto it. `0` means "same as the
  host port".
- **Volumes:** `container_volumes` is a map of `{ named_volume: mount_path }`.
  Named volumes hold your app's data and **survive rebuilds, restarts and
  updates**. Use them for anything the user must not lose (databases, uploads).
- **Environment:** `container_env` is a map of `{ VAR: value }` passed into the
  container. Use it for non-secret configuration. Do not put secrets that must
  not be visible in a config file here.
- **Restart policy:** `restart_policy` (for example `unless-stopped`) is applied
  to the container so the app comes back after a reboot.

---

## Versioning and pinning: tag vs digest

`image_reference` accepts either form:

- **Tag**, for example `ghcr.io/owner/my-app:1.2.3`. Readable and easy to bump.
  A tag is a moving label: if you republish `1.2.3`, machines that re-pull get
  the new contents. Use an **immutable, versioned tag per release** (never rely
  on `latest` for a shipped config) so a re-pull is predictable.
- **Digest**, for example `ghcr.io/owner/my-app@sha256:abcd...`. A digest names
  the exact image bytes and can never change. Pin a digest when you want a
  guarantee that every machine runs byte-for-byte the same image, for example
  for regulated or reproducible deployments.

Rule of thumb: ship a versioned tag for normal releases, and switch to a digest
when immutability matters more than readability. When you cut a new release, you
bump `image_reference` (and `app_version`) in the config you distribute.

---

## Release artifacts

For `image` mode you publish two things, one required and one optional.

### 1. Publish the image to a registry (required)

Build a multi-architecture image and push it to a public registry such as GitHub
Container Registry (GHCR):

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/owner/my-app:1.2.3 \
  --push .
```

- Publish **multi-arch** (at least `linux/amd64` and `linux/arm64`) so the
  engine can pick the right variant on each user's machine. If a user's platform
  has no variant, the launcher reports exactly that instead of a raw error.
- Make the package **public**, or your users need
  `use_registry_credentials: true` in the config plus a `docker login`. By
  default the launcher does not touch registry credentials.

### 2. Ship a `docker save` archive (optional, for offline installs)

If some of your users have no reliable internet, also ship an archive built with
`docker save`:

```bash
docker save ghcr.io/owner/my-app:1.2.3 -o my-app.tar
```

Point `image_archive` at it (relative paths resolve against `install_dir`, which
for file-loaded configs defaults to the config file's own directory). When the
archive file is present, the launcher loads the image from it and never contacts
the registry. If the archive is configured but missing, the launcher falls back
to pulling and its readiness gate flags the unreadable file, naming the
directory it searched.

The archive must contain the exact `image_reference` (build it with
`docker save <image_reference>`), otherwise the launcher fails early and tells
you the archive does not contain the configured reference.

---

## The update path

When you publish a new version, users move to it in one step.

- **GUI:** the **Update** button.
- **CLI:** `docker-app-launcher --update`.

The launcher performs: stop, re-acquire (in `image` mode it re-pulls
`image_reference`; in the build modes it rebuilds), start, and a health check.
**Named volumes survive**, so user data is preserved, and in `image` mode the
previous image remains on disk, so a failed update can be rolled back. If the
health check fails after the update, the launcher prints a rollback hint that
names the previous image so the user can return to the working version.

For your users, the recipe to release an update is simply: publish the new image
(and archive, if you ship one), then hand out a config whose `image_reference`
and `app_version` point at the new release.

> Historical note: before the one-step update existed, the documented path was
> **Stop, then Start**, because Start on a stopped stack re-pulls the reference
> and replaces the container. That still works and does the same thing under the
> hood; `--update` and the **Update** button just wrap it into a single action
> with a health check and a rollback hint.

---

## See also

- [End-user quickstart](quickstart-end-user.md), the guide you can point your
  users at.
- The project [README](../README.md) for the complete configuration reference,
  the CLI, and the diagnostics (`--doctor`, `--support-bundle`, `--json`).
- [docs/environment-matrix.md](environment-matrix.md) for the supported and
  tested environment cells per mode.
