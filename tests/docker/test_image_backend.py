"""Tests for the image deployment mode (#78) - docker-py mocked, no daemon."""

from __future__ import annotations

from typing import Any

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import build_readiness, image_backend, lifecycle, py_client


class _FakeImages:
    def __init__(self, present: bool = False) -> None:
        self._present = present
        self.load_contains_reference = True
        self.loaded: list[bytes] = []

    def get(self, ref: str) -> Any:
        if not self._present:
            raise RuntimeError(f"no such image: {ref}")
        return object()

    def load(self, fh: Any) -> Any:
        self.loaded.append(fh.read())
        if self.load_contains_reference:
            self._present = True
        return []


class _FakePullApi:
    def __init__(self, chunks: list[dict[str, Any]], exc: BaseException | None = None) -> None:
        self._chunks = chunks
        self._exc = exc
        self.pull_args: tuple[Any, ...] = ()
        self._auth_configs: Any = None
        self._proxy_configs: Any = None

    def pull(self, repository: str, tag: str | None = None, **kwargs: Any):
        self.pull_args = (repository, tag)
        if self._exc is not None:
            raise self._exc
        yield from self._chunks


class _FakeRunContainers:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        raise RuntimeError("no such container")

    def run(self, image: str, **kwargs: Any) -> Any:
        self.run_kwargs = {"image": image, **kwargs}
        return object()


class _FakePullClient:
    def __init__(
        self,
        chunks: list[dict[str, Any]] | None = None,
        *,
        image_present: bool = False,
        pull_exc: BaseException | None = None,
    ) -> None:
        self.api = _FakePullApi(chunks or [], exc=pull_exc)
        self.images = _FakeImages(present=image_present)
        self.containers = _FakeRunContainers()
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def pcfg(tmp_path):
    return LauncherConfig(
        app_name="Image App",
        deployment_mode="image",
        image_reference="ghcr.io/owner/app:2.0.0",
        install_dir=str(tmp_path),
        default_port=8080,
        locale="en",
    ).resolve()


def _wire(monkeypatch, client: _FakePullClient) -> None:
    monkeypatch.setattr(py_client, "get_client", lambda *a, **k: client)
    monkeypatch.setattr(py_client, "available", lambda: True)


class TestImageUp:
    def test_registry_pull_streams_layers_and_runs(self, pcfg, monkeypatch) -> None:
        client = _FakePullClient(
            [
                {"status": "Pulling from owner/app", "id": ""},
                {"status": "Downloading", "id": "abc123"},
                {"status": "Pull complete", "id": "abc123"},
            ]
        )
        _wire(monkeypatch, client)
        seen: list[str] = []
        rc, detail = image_backend.up(pcfg, on_output=seen.append)
        assert rc == 0 and detail == ""
        assert client.api.pull_args == ("ghcr.io/owner/app", "2.0.0")
        assert any("abc123: Downloading" in line for line in seen), "layer progress must reach the panel"
        run = client.containers.run_kwargs
        assert run["image"] == "ghcr.io/owner/app:2.0.0"
        assert run["ports"] == {"8080/tcp": ("127.0.0.1", 8080)}  # localhost-pinned tuple, never a bare int (#111)
        assert client.closed

    def test_digest_reference_parses(self, pcfg, monkeypatch) -> None:
        pcfg.image_reference = "ghcr.io/owner/app@sha256:" + "a" * 64
        client = _FakePullClient([{"status": "ok"}])
        _wire(monkeypatch, client)
        rc, _ = image_backend.up(pcfg)
        assert rc == 0
        repo, tag = client.api.pull_args
        assert repo == "ghcr.io/owner/app" and tag.startswith("sha256:")

    def test_auth_not_resolved_by_default(self, pcfg, monkeypatch) -> None:
        # #77 consistency: the pull must not touch the user's credential store.
        client = _FakePullClient([{"status": "ok"}])
        _wire(monkeypatch, client)
        image_backend.up(pcfg)
        replaced = client.api._auth_configs
        assert replaced is not None and replaced.get_all_credentials() == {}
        # The PULL path wraps _auth_configs in docker-py's AuthConfig, which
        # requires a real dict shape - a plain object breaks with "argument
        # of type '_NoRegistryAuth' is not iterable" (#78 live-proof find).
        assert replaced.is_empty is False, "empty/falsy would re-arm the broken credsStore reload (#77)"
        assert isinstance(replaced, dict) and dict(replaced) == {"auths": {}}

    def test_opt_in_keeps_auth(self, pcfg, monkeypatch) -> None:
        pcfg.use_registry_credentials = True
        client = _FakePullClient([{"status": "ok"}])
        _wire(monkeypatch, client)
        image_backend.up(pcfg)
        assert client.api._auth_configs is None, "opt-in must leave docker-py's auth untouched"

    def test_missing_platform_variant_is_actionable(self, pcfg, monkeypatch) -> None:
        client = _FakePullClient([{"error": "no matching manifest for linux/arm64 in the manifest list entries"}])
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg)
        assert rc == 1
        assert "platform" in detail and "multi-arch" in detail
        assert pcfg.image_reference in detail

    def test_network_error_with_local_image_falls_back(self, pcfg, monkeypatch) -> None:
        # Offline start MUST work when the image is already local.
        client = _FakePullClient(image_present=True, pull_exc=OSError("dial tcp: no such host"))
        _wire(monkeypatch, client)
        seen: list[str] = []
        rc, _ = image_backend.up(pcfg, on_output=seen.append)
        assert rc == 0
        assert any("local image" in line for line in seen)
        assert client.containers.run_kwargs["image"] == pcfg.image_reference

    def test_network_error_without_local_image_is_named(self, pcfg, monkeypatch) -> None:
        client = _FakePullClient(image_present=False, pull_exc=OSError("dial tcp: no such host"))
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg)
        assert rc == 1
        assert "internet connection" in detail

    def test_archive_wins_over_pull(self, pcfg, monkeypatch, tmp_path) -> None:
        archive = tmp_path / "app-image.tar"
        archive.write_bytes(b"tarbytes")
        pcfg.image_archive = str(archive)

        def no_pull(*a, **k):
            raise AssertionError("archive present - the registry must not be contacted")

        client = _FakePullClient()
        client.api.pull = no_pull  # type: ignore[method-assign]
        _wire(monkeypatch, client)
        rc, _ = image_backend.up(pcfg)
        assert rc == 0
        assert client.images.loaded == [b"tarbytes"], "the archive bytes must be loaded via the API"

    def test_archive_without_the_reference_is_a_hard_error(self, pcfg, monkeypatch, tmp_path) -> None:
        # A loaded archive that does not yield image_reference must fail with
        # the file named - never a later raw ImageNotFound at run time.
        archive = tmp_path / "wrong.tar"
        archive.write_bytes(b"tarbytes")
        pcfg.image_archive = str(archive)
        client = _FakePullClient()
        client.images.load_contains_reference = False
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg)
        assert rc == 1
        assert "wrong.tar" in detail and pcfg.image_reference in detail
        assert client.containers.run_kwargs == {}, "no container may start from a wrong archive"

    def test_missing_archive_falls_back_to_pull(self, pcfg, monkeypatch, tmp_path) -> None:
        pcfg.image_archive = str(tmp_path / "gone.tar")
        client = _FakePullClient([{"status": "ok"}])
        _wire(monkeypatch, client)
        rc, _ = image_backend.up(pcfg)
        assert rc == 0 and client.api.pull_args[0] == "ghcr.io/owner/app"


class TestImageBlockers:
    def test_ready_with_reference(self, pcfg, monkeypatch) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        assert build_readiness.image_blockers(pcfg) == []

    def test_no_source_is_a_blocker(self, pcfg, monkeypatch) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        pcfg.image_reference = ""
        blockers = build_readiness.image_blockers(pcfg)
        assert any("image_reference" in b for b in blockers)

    def test_unreadable_archive_is_a_blocker(self, pcfg, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        pcfg.image_archive = str(tmp_path / "missing.tar")
        blockers = build_readiness.image_blockers(pcfg)
        assert any("not readable" in b for b in blockers)

    def test_no_dockerpy_is_a_blocker(self, pcfg, monkeypatch) -> None:
        monkeypatch.setattr(py_client, "available", lambda: False)
        assert any("docker-py" in b for b in build_readiness.image_blockers(pcfg))

    def test_no_compose_or_buildx_requirements(self, pcfg, monkeypatch) -> None:
        # The whole point of the mode: the toolchain matrix does not apply.
        monkeypatch.setattr(py_client, "available", lambda: True)
        blockers = build_readiness.image_blockers(pcfg)
        assert not any("compose" in b.lower() or "buildx" in b.lower() for b in blockers)


class TestLifecycleImageDispatch:
    def _base(self, monkeypatch, states: list[str]) -> None:
        it = iter(states)
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: next(it))
        monkeypatch.setattr(lifecycle, "check_port", lambda p, **k: (True, "free"))
        monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))
        monkeypatch.setattr(lifecycle, "_ensure_image_ready", lambda c: None)

        def no_compose(*a, **k):
            raise AssertionError("compose must not run in image mode")

        monkeypatch.setattr(lifecycle, "_stream_compose", no_compose)

    def test_install_routes_to_image_backend(self, pcfg, monkeypatch) -> None:
        self._base(monkeypatch, ["not_installed", "running"])
        monkeypatch.setattr(image_backend, "image_present", lambda c: True)
        monkeypatch.setattr(image_backend, "up", lambda c, **k: (0, ""))
        ok, msg = lifecycle.install(pcfg)
        assert ok is True and "ready" in msg

    def test_install_prewarns_network_only_when_image_absent(self, pcfg, monkeypatch) -> None:
        self._base(monkeypatch, ["not_installed", "running"])
        monkeypatch.setattr(image_backend, "image_present", lambda c: False)
        monkeypatch.setattr(image_backend, "up", lambda c, **k: (0, ""))
        steps: list[str] = []
        ok, _ = lifecycle.install(pcfg, on_step=steps.append)
        assert ok is True
        assert any("internet" in s.lower() for s in steps), "network pre-warning must fire when the image is absent"

    def test_install_no_prewarn_when_image_present(self, pcfg, monkeypatch) -> None:
        self._base(monkeypatch, ["not_installed", "running"])
        monkeypatch.setattr(image_backend, "image_present", lambda c: True)
        monkeypatch.setattr(image_backend, "up", lambda c, **k: (0, ""))
        steps: list[str] = []
        lifecycle.install(pcfg, on_step=steps.append)
        assert not any("internet" in s.lower() for s in steps)

    def test_app_logs_uses_the_container_tail(self, pcfg, monkeypatch) -> None:
        from docker_app_launcher.docker import dockerfile_backend

        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        monkeypatch.setattr(dockerfile_backend, "tail_logs", lambda c, *, lines: (True, "pulled ready"))
        ok, text = lifecycle.app_logs(pcfg)
        assert ok is True and text == "pulled ready"


class TestArchiveBlockerNamesTheBase:
    """#83: the gate states WHERE it searched for the archive."""

    def test_unreadable_archive_names_the_searched_directory(self, pcfg, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        pcfg.image_archive = "sub/missing.tar"
        blockers = build_readiness.image_blockers(pcfg)
        assert len(blockers) == 1
        assert "missing.tar" in blockers[0]
        assert str(tmp_path / "sub") in blockers[0], "the searched directory must be named"

    def test_cwd_fallback_relative_archive_names_the_missing_base(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        monkeypatch.chdir(tmp_path)
        cfg = LauncherConfig(
            app_name="Image App",
            deployment_mode="image",
            image_reference="ghcr.io/owner/app:2.0.0",
            image_archive="missing.tar",
            locale="en",
        ).resolve()
        blockers = build_readiness.image_blockers(cfg)
        assert len(blockers) == 1
        assert "install_dir" in blockers[0], "the missing base is the actionable fact, not the file"

    def test_absolute_archive_never_blames_the_base(self, pcfg, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(py_client, "available", lambda: True)
        pcfg.install_dir = ""
        pcfg.image_archive = str(tmp_path / "abs-missing.tar")
        blockers = build_readiness.image_blockers(pcfg)
        assert len(blockers) == 1
        assert "install_dir" not in blockers[0]
        assert str(tmp_path) in blockers[0]


class TestRegistryAccessClassification:
    """#87: a failing registry token flow (GHCR-style denied/unauthorized)
    must name the REGISTRY ACCESS as the cause - never a raw library error."""

    @pytest.mark.parametrize(
        "raw",
        [
            "denied: requested access to the resource is denied",
            "unauthorized: authentication required",
            "pull access denied for ghcr.io/owner/app, repository does not exist or may require 'docker login'",
        ],
    )
    def test_denied_pull_names_the_registry_access(self, pcfg, monkeypatch, raw) -> None:
        client = _FakePullClient([{"error": raw}])
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg)
        assert rc == 1
        assert "registry" in detail.lower(), "the cause must be named as registry access"
        assert pcfg.image_reference in detail
        assert "use_registry_credentials" in detail, "the private-image path must be named"

    def test_denied_exception_is_classified_too(self, pcfg, monkeypatch) -> None:
        client = _FakePullClient(pull_exc=RuntimeError("unauthorized: authentication required"))
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg)
        assert rc == 1 and "registry" in detail.lower()


class TestPullCancel:
    """#98: a cancel request ENDS the pull - the stream stops being consumed
    between chunks (the daemon aborts remaining downloads when the request
    closes), no container is started, and the message says what remains:
    already fetched layers stay cached and speed up the next attempt
    (deliberate decision, not a side effect)."""

    def test_cancel_between_chunks_stops_the_pull(self, pcfg, monkeypatch) -> None:
        seen = {"chunks": 0}

        class _EndlessApi(_FakePullApi):
            def pull(self, repository, tag=None, **kwargs):
                self.pull_args = (repository, tag)
                while True:
                    seen["chunks"] += 1
                    yield {"status": "Downloading", "id": "layer1"}

        client = _FakePullClient()
        client.api = _EndlessApi([])
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg, should_cancel=lambda: seen["chunks"] >= 3)
        assert rc == 1
        assert "cancel" in detail.lower(), f"the result must say it was cancelled: {detail!r}"
        assert seen["chunks"] <= 4, "the stream must stop being consumed promptly after the request"
        assert client.containers.run_kwargs == {}, "no container may start from a cancelled pull"

    def test_cancelled_message_names_the_kept_layers(self, pcfg, monkeypatch) -> None:
        class _EndlessApi(_FakePullApi):
            def pull(self, repository, tag=None, **kwargs):
                while True:
                    yield {"status": "Downloading", "id": "l"}

        client = _FakePullClient()
        client.api = _EndlessApi([])
        _wire(monkeypatch, client)
        rc, detail = image_backend.up(pcfg, should_cancel=lambda: True)
        assert rc == 1 and "layer" in detail.lower(), "kept layers are a decision, named in the message"

    def test_no_cancel_callback_keeps_old_behavior(self, pcfg, monkeypatch) -> None:
        client = _FakePullClient([{"status": "ok"}])
        _wire(monkeypatch, client)
        rc, _ = image_backend.up(pcfg)
        assert rc == 0
