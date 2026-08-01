"""REAL old-engine cell for the image mode (#84) - the mode's central promise.

Launched by ``run_image_mode_old_engine_integration.sh``: a PINNED
20.10-class engine (``docker:<tag>-dind``) runs as a throwaway daemon; the
runner PROVES the environment ships neither a compose plugin nor buildx
before any test starts (otherwise this cell would only show the mode also
works there, not that it works WITHOUT the build toolchain). The tests then
drive ``image_backend.up`` against that engine over ``DOCKER_HOST`` for BOTH
acquisition sources - registry pull and local archive load - each followed
by a container start and a real HTTP endpoint check.

A failure here is a FINDING about the documented minimum engine generation,
never a reason to bend the test (#84).

Gates: ``DAL_IMAGE_OLD_ENGINE=1`` (never runs in the Docker-free suite);
``DOCKER_HOST`` points at the old engine; ``DAL_OLD_ENGINE_HTTP_HOST`` is
the address where published ports are reachable.
"""

from __future__ import annotations

import contextlib
import os
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from docker_app_launcher.config import LauncherConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_IMAGE_OLD_ENGINE") != "1",
        reason="old-engine image-mode cell: only via run_image_mode_old_engine_integration.sh",
    ),
]

# Tiny, multi-arch, serves HTTP on :80 - the same image the #78 live proof used.
_REF = "traefik/whoami:v1.10"
# The SAME app from GHCR (#87): users of GHCR-publishing consumers hit its
# anonymous token flow, which differs from Docker Hub's - measured, not assumed.
#
# The DEFAULT is deliberately a generic public image: this cell must never
# depend on a consumer release being published (#89). The three knobs point it
# at a REAL consumer reference for a one-time measurement - the result belongs
# in the issue, not in the default:
#
#   DAL_OLD_ENGINE_GHCR_REF="ghcr.io/owner/app:1.2.3" \
#   DAL_OLD_ENGINE_GHCR_CONTAINER_PORT=18001 \
#   DAL_OLD_ENGINE_GHCR_PATH=/api/health \
#     tests/integration/run_image_mode_old_engine_integration.sh
_GHCR_REF = os.environ.get("DAL_OLD_ENGINE_GHCR_REF", "ghcr.io/traefik/whoami:v1.10")
_GHCR_CONTAINER_PORT = int(os.environ.get("DAL_OLD_ENGINE_GHCR_CONTAINER_PORT", "80"))
_GHCR_PATH = os.environ.get("DAL_OLD_ENGINE_GHCR_PATH", "/")
_PORT = 18124
_HTTP_HOST = os.environ.get("DAL_OLD_ENGINE_HTTP_HOST", "127.0.0.1")


def _config(tmp_path: Path, archive: str = "", reference: str = _REF, container_port: int = 80) -> LauncherConfig:
    from docker_app_launcher.config import LauncherConfig

    return LauncherConfig(
        app_name="Old Engine Cell",
        container_name="dal-old-engine-cell",
        deployment_mode="image",
        image_reference=reference,
        image_archive=archive,
        install_dir=str(tmp_path),
        default_port=_PORT,
        container_port=container_port,
        # DELIBERATELY open (#111): this cell reaches the published port from
        # OUTSIDE the dind engine's network namespace (DAL_OLD_ENGINE_HTTP_HOST
        # is the dind container's IP), which the localhost default correctly
        # forbids. The default itself is pinned in tests/docker/test_bind_address.py
        # and at a running container in the lifecycle matrix; what this cell
        # measures is the image mode on an OLD ENGINE, not the bind policy.
        bind_address="0.0.0.0",
        locale="en",
    ).resolve()


def _http_ok(path: str = "/") -> bool:
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://{_HTTP_HOST}:{_PORT}{path}", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - retry until the container answers
            time.sleep(0.5)
    return False


def _http_body(path: str = "/") -> str:
    with urllib.request.urlopen(f"http://{_HTTP_HOST}:{_PORT}{path}", timeout=5) as r:
        return str(r.read().decode("utf-8", "replace"))


def _client() -> Any:
    from docker_app_launcher.docker import py_client

    return py_client.get_client()


def _cleanup(client: Any) -> None:
    with contextlib.suppress(Exception):  # absent is fine
        client.containers.get("dal-old-engine-cell").remove(force=True)
    with contextlib.suppress(Exception):
        client.images.remove(_REF, force=True)


class TestOldEngineEnvironment:
    def test_engine_is_the_pinned_old_generation(self) -> None:
        client = _client()
        try:
            version = client.version()
        finally:
            client.close()
        engine = version.get("Version", "")
        expected = os.environ.get("DAL_OLD_ENGINE_EXPECT", "20.10.")
        assert engine.startswith(expected), f"cell must run against {expected}x, got {engine!r}"
        assert float(version.get("ApiVersion", "0")) <= 1.41, "20.10-class API expected"


class TestRegistrySource:
    def test_pull_run_and_http(self, tmp_path: Path) -> None:
        from docker_app_launcher.docker import image_backend

        client = _client()
        try:
            _cleanup(client)
        finally:
            client.close()
        lines: list[str] = []
        rc, detail = image_backend.up(_config(tmp_path), on_output=lines.append)
        assert rc == 0, f"registry source failed on the old engine: {detail}"
        assert any("Pull" in ln or "Download" in ln for ln in lines), "layer progress must stream"
        assert _http_ok(), "published endpoint did not answer on the old engine"


class TestGhcrRegistrySource:
    def test_anonymous_ghcr_pull_run_and_http(self, tmp_path: Path) -> None:
        """#87: GHCR's anonymous token flow on the old engine, measured.

        Credential-freedom is twofold: use_registry_credentials defaults to
        False, so image_backend neutralizes docker-py's auth (#77 sentinel),
        and the throwaway dind daemon has no stored logins at all.
        """
        from docker_app_launcher.docker import image_backend

        client = _client()
        try:
            _cleanup(client)
            with contextlib.suppress(Exception):
                client.images.remove(_GHCR_REF, force=True)
        finally:
            client.close()
        config = _config(tmp_path, reference=_GHCR_REF, container_port=_GHCR_CONTAINER_PORT)
        assert config.use_registry_credentials is False, "the pull must be credential-free"
        lines: list[str] = []
        rc, detail = image_backend.up(config, on_output=lines.append)
        assert rc == 0, f"anonymous GHCR pull failed on the old engine: {detail}"
        assert any("Pull" in ln or "Download" in ln for ln in lines), "layer progress must stream"
        assert _http_ok(_GHCR_PATH), f"published endpoint did not answer after the GHCR pull ({_GHCR_REF})"
        # Say WHAT was measured, not just that it passed: with the reference
        # overridable (#89), a green run whose default silently applied would
        # look exactly like a green run against the consumer's real image.
        # The digest is the identity the pull actually resolved to.
        client = _client()
        try:
            image = client.images.get(_GHCR_REF)
            print(
                f"MEASURED anonymous GHCR pull: reference={_GHCR_REF} "
                f"repo_digests={image.attrs.get('RepoDigests')} "
                f"size_on_disk={image.attrs.get('Size')} "
                f"container_port={_GHCR_CONTAINER_PORT} path={_GHCR_PATH} "
                f"response={_http_body(_GHCR_PATH)[:200]!r}"
            )
        finally:
            client.close()
        client = _client()
        try:
            _cleanup(client)
            with contextlib.suppress(Exception):
                client.images.remove(_GHCR_REF, force=True)
        finally:
            client.close()

    def test_refused_ghcr_pull_names_the_registry_access(self, tmp_path: Path) -> None:
        """#87 error case: a refused token flow (missing/private repository)
        must name the registry access, never a raw library error."""
        from docker_app_launcher.docker import image_backend

        config = _config(tmp_path, reference="ghcr.io/astrapi69/dal-does-not-exist:1.0.0")
        rc, detail = image_backend.up(config)
        assert rc == 1
        assert "registry" in detail.lower(), f"cause must be the registry access, got: {detail}"
        assert "use_registry_credentials" in detail, "the private-image path must be named"


class TestArchiveSource:
    def test_load_run_and_http(self, tmp_path: Path) -> None:
        from docker_app_launcher.docker import image_backend

        client = _client()
        try:
            # Self-sufficient: make sure the image exists ON the old engine,
            # export it via the API, then remove it so only the archive can
            # provide it - the registry-free path.
            try:
                image = client.images.get(_REF)
            except Exception:  # noqa: BLE001 - pull on demand
                repo, _, tag = _REF.partition(":")
                image = client.images.pull(repo, tag=tag)
            archive = tmp_path / "whoami.tar"
            with archive.open("wb") as fh:
                for chunk in image.save(named=True):
                    fh.write(chunk)
            _cleanup(client)
        finally:
            client.close()

        lines: list[str] = []
        rc, detail = image_backend.up(_config(tmp_path, archive=str(archive)), on_output=lines.append)
        assert rc == 0, f"archive source failed on the old engine: {detail}"
        assert any("archive" in ln for ln in lines), "the archive path must be taken"
        assert _http_ok(), "published endpoint did not answer after the archive load"

        client = _client()
        try:
            _cleanup(client)
        finally:
            client.close()
