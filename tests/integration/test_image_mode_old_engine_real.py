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

import os
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DAL_IMAGE_OLD_ENGINE") != "1",
        reason="old-engine image-mode cell: only via run_image_mode_old_engine_integration.sh",
    ),
]

# Tiny, multi-arch, serves HTTP on :80 - the same image the #78 live proof used.
_REF = "traefik/whoami:v1.10"
_PORT = 18124
_HTTP_HOST = os.environ.get("DAL_OLD_ENGINE_HTTP_HOST", "127.0.0.1")


def _config(tmp_path: Path, archive: str = ""):
    from docker_app_launcher.config import LauncherConfig

    return LauncherConfig(
        app_name="Old Engine Cell",
        container_name="dal-old-engine-cell",
        deployment_mode="image",
        image_reference=_REF,
        image_archive=archive,
        install_dir=str(tmp_path),
        default_port=_PORT,
        container_port=80,
        locale="en",
    ).resolve()


def _http_ok() -> bool:
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://{_HTTP_HOST}:{_PORT}/", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - retry until the container answers
            time.sleep(0.5)
    return False


def _client():
    from docker_app_launcher.docker import py_client

    return py_client.get_client()


def _cleanup(client) -> None:
    for fn in (
        lambda: client.containers.get("dal-old-engine-cell").remove(force=True),
        lambda: client.images.remove(_REF, force=True),
    ):
        try:
            fn()
        except Exception:  # noqa: BLE001 - absent is fine
            pass


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
    def test_pull_run_and_http(self, tmp_path) -> None:
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


class TestArchiveSource:
    def test_load_run_and_http(self, tmp_path) -> None:
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
