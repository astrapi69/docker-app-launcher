"""Tests for the one-step update action (#92): stop -> re-acquire -> start ->
health, with a rollback hint on health failure.

``update`` delegates the re-acquire to :func:`lifecycle.start` (image mode
re-pulls the reference, the build modes rebuild), then adds the health gate
and the rollback hint that ``start`` does not do. Mocks patch the OWNING
module (lifecycle); the facade only re-exports.
"""

from __future__ import annotations

import pytest

from docker_app_launcher import i18n
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import lifecycle

_Result = tuple[bool, str]


@pytest.fixture
def image_config(tmp_path) -> LauncherConfig:
    """A resolved image-mode config (rollback hint uses the manifest identity)."""
    cfg = LauncherConfig(
        app_name="Test App",
        container_name="test-app",
        default_port=8080,
        config_dir=str(tmp_path / ".test-app"),
        deployment_mode="image",
        image_reference="ghcr.io/owner/test-app:1.2.3",
        locale="en",
    )
    cfg.resolve()
    return cfg


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str = "running",
    stop: _Result = (True, "stopped"),
    start: _Result = (True, "started"),
    health: _Result = (True, "ok"),
) -> dict[str, int]:
    """Pin the collaborators update() calls out to, returning a call recorder."""
    calls: dict[str, int] = {"stop": 0, "start": 0, "health": 0}
    monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
    monkeypatch.setattr(lifecycle, "get_state", lambda c: state)

    def _stop(c: LauncherConfig) -> _Result:
        calls["stop"] += 1
        return stop

    def _start(c: LauncherConfig, **k: object) -> _Result:
        calls["start"] += 1
        return start

    def _health(c: LauncherConfig, port: int | None = None) -> _Result:
        calls["health"] += 1
        return health

    monkeypatch.setattr(lifecycle, "stop", _stop)
    monkeypatch.setattr(lifecycle, "start", _start)
    monkeypatch.setattr(lifecycle, "health_check", _health)
    return calls


class TestUpdateGuards:
    def test_docker_down(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (False, "down"))
        ok, msg = lifecycle.update(config)
        assert ok is False
        assert msg == i18n.t("docker_unavailable", config)

    def test_not_installed(self, config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "not_installed")
        ok, msg = lifecycle.update(config)
        assert ok is False
        assert msg == i18n.t("update_not_installed", config)


class TestUpdateFlow:
    def test_happy_path_reacquires_and_healthchecks(self, config, monkeypatch) -> None:
        calls = _wire(monkeypatch, state="running")
        ok, msg = lifecycle.update(config)
        assert ok is True
        assert msg == i18n.t("update_done", config)
        # stop -> start (re-acquire) -> health, each exactly once.
        assert calls == {"stop": 1, "start": 1, "health": 1}

    def test_from_stopped_state(self, config, monkeypatch) -> None:
        # A stopped stack updates too: stop() short-circuits (already stopped),
        # start() re-acquires and runs it.
        calls = _wire(monkeypatch, state="stopped", stop=(True, "already stopped"))
        ok, _ = lifecycle.update(config)
        assert ok is True
        assert calls["start"] == 1 and calls["health"] == 1

    def test_stop_failure_aborts_before_start(self, config, monkeypatch) -> None:
        calls = _wire(monkeypatch, state="running", stop=(False, "stop boom"))
        ok, msg = lifecycle.update(config)
        assert ok is False
        assert msg == "stop boom"
        assert calls["start"] == 0  # never re-acquired on a failed stop

    def test_start_failure_is_passed_through(self, config, monkeypatch) -> None:
        # start() already classifies build/pull failures; update() surfaces them.
        calls = _wire(monkeypatch, state="running", start=(False, "pull refused"))
        ok, msg = lifecycle.update(config)
        assert ok is False
        assert msg == "pull refused"
        assert calls["health"] == 0  # no health probe when the re-acquire failed


class TestUpdateRollbackHint:
    def test_health_failure_image_mode_names_previous_image(self, image_config, monkeypatch) -> None:
        # The manifest recorded the outgoing image identity (#80); a failed
        # update must point back at it (#88: the old image survives a re-pull).
        monkeypatch.setattr(
            lifecycle,
            "read_manifest",
            lambda c: {"image_id": "sha256:oldid", "image_reference": "ghcr.io/owner/test-app:1.2.2"},
        )
        _wire(monkeypatch, state="running", health=(False, "no route to host"))
        ok, msg = lifecycle.update(image_config)
        assert ok is False
        # both the health detail AND the rollback anchor are visible.
        assert "no route to host" in msg
        assert "sha256:oldid" in msg
        assert "ghcr.io/owner/test-app:1.2.2" in msg

    def test_health_failure_build_mode_generic_hint(self, config, monkeypatch) -> None:
        # compose/dockerfile keep no separate previous-image handle; the hint
        # is the generic reinstall line, not an image reference.
        monkeypatch.setattr(lifecycle, "read_manifest", lambda c: {})
        _wire(monkeypatch, state="running", health=(False, "HTTP 500"))
        ok, msg = lifecycle.update(config)
        assert ok is False
        assert "HTTP 500" in msg
        assert i18n.t("update_rollback_generic", config) in msg


class TestUpdateMessageVisibility:
    def test_steps_reach_the_step_callback(self, config, monkeypatch) -> None:
        # Every phase is announced through on_step so it lands in the log panel
        # AND the persistent log (no silent phase).
        _wire(monkeypatch, state="running")
        steps: list[str] = []
        ok, _ = lifecycle.update(config, on_step=steps.append)
        assert ok is True
        assert i18n.t("update_stopping", config) in steps
        assert i18n.t("update_fetching", config) in steps


class TestUpdateCancelLeavesStoppedStateNamed:
    """#98: the trickiest cancel - update STOPPED the app before re-acquiring.
    The message must say the app is stopped now and name Start as the next
    step (the previous image is still local, #88)."""

    def test_cancel_during_reacquire_names_the_stopped_state(self, image_config, monkeypatch) -> None:
        monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
        monkeypatch.setattr(lifecycle, "get_state", lambda c: "running")
        monkeypatch.setattr(lifecycle, "read_manifest", lambda c: {})
        monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))
        monkeypatch.setattr(lifecycle, "start", lambda c, **k: (False, "cancelled by request - layers cached"))
        cancelled = {"v": True}
        ok, msg = lifecycle.update(image_config, should_cancel=lambda: cancelled["v"])
        assert ok is False
        assert "STOPPED" in msg or "GESTOPPT" in msg.upper() or "stopped" in msg.lower()
        assert "Start" in msg, "the next step must be named"
