"""#101: the internal-port rebuild is cancellable, in every frontend.

It was the one long-running action whose rebuild ran its OWN compose call
without the #60 cancel plumbing - the user faced a minutes-long rebuild with a
cancel control that was deliberately not shown, and the reason lived as prose
in a comment (an intent in parentheses is not tracking, #98 review).

Three things have to hold together, so all three are pinned here: the action
layer must forward the cancel predicate and END on it, the presentation layer
must declare the action cancellable with an honest post-state message, and each
of the three frontends must actually arm the signal when it starts the rebuild.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from docker_app_launcher import actions, i18n, ui_model
from docker_app_launcher.config import SUPPORTED_LOCALES, LauncherConfig
from docker_app_launcher.docker import lifecycle
from docker_app_launcher.docker.command_runner import BuildCancelled


@pytest.fixture
def compose_config(tmp_path) -> LauncherConfig:
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    (install_dir / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    config = LauncherConfig(
        app_name="Internal Port App",
        install_dir=str(install_dir),
        config_dir=str(tmp_path / ".cfg"),
        default_port=8080,
        locale="en",
    ).resolve()
    config.env_internal_port_keys = {"backend": "APP_BACKEND_PORT"}
    return config


@pytest.fixture
def running_stack(monkeypatch):
    states = iter(["running", "running"])
    monkeypatch.setattr(lifecycle, "check_docker", lambda: (True, "ok"))
    monkeypatch.setattr(lifecycle, "get_state", lambda c: next(states))
    monkeypatch.setattr(lifecycle, "stop", lambda c: (True, "stopped"))
    monkeypatch.setattr(lifecycle, "_ensure_build_ready", lambda c: None)
    monkeypatch.setattr(lifecycle, "health_check", lambda c, port=None: (True, "ok"))


class TestActionLayerForwardsTheCancel:
    def test_the_rebuild_receives_the_predicate(self, compose_config, monkeypatch, running_stack) -> None:
        seen: dict[str, Any] = {}

        def fake_stream(c, *args, **kwargs):
            seen["should_cancel"] = kwargs.get("should_cancel")
            return (0, "")

        monkeypatch.setattr(lifecycle, "_stream_compose", fake_stream)

        def predicate() -> bool:
            return False

        ok, _ = lifecycle.change_internal_port(compose_config, "backend", 9001, should_cancel=predicate)
        assert ok
        assert seen["should_cancel"] is predicate, "the rebuild ran without the cancel plumbing (#101)"

    def test_a_cancel_ends_the_action_as_an_outcome(self, compose_config, monkeypatch, running_stack) -> None:
        def cancelled_stream(c, *args, **kwargs):
            raise BuildCancelled("stopped by request")

        monkeypatch.setattr(lifecycle, "_stream_compose", cancelled_stream)
        ok, msg = lifecycle.change_internal_port(compose_config, "backend", 9001, should_cancel=lambda: True)
        assert not ok, "a cancel is an outcome, not a success"
        # The honest post-state: the app is STOPPED, because the rebuild path
        # stops it first. Saying only 'cancelled' would leave the user guessing
        # why nothing runs.
        assert "STOPPED" in msg or "stopped" in msg
        assert "Traceback" not in msg

    def test_the_facade_exposes_the_same_signature(self) -> None:
        # CLI <-> GUI parity: both call this one function, so the parameter
        # cannot exist on only one of the two import paths.
        assert "should_cancel" in inspect.signature(actions.change_internal_port).parameters


class TestPresentationLayerDeclaresItCancellable:
    def test_registered_in_the_honesty_map(self) -> None:
        assert "change_internal_port" in ui_model.CANCELLABLE_ACTIONS, (
            "the rebuild is cancellable now - the map is what makes the control appear (#101)"
        )

    def test_its_message_exists_in_every_catalog(self) -> None:
        key = ui_model.CANCELLABLE_ACTIONS["change_internal_port"]
        for code in SUPPORTED_LOCALES:
            assert key in i18n.STRINGS[code], f"{code}.yaml lacks the cancel message {key}"

    def test_the_message_names_the_state_the_user_is_left_in(self) -> None:
        config = LauncherConfig(app_name="X", locale="en").resolve()
        key = ui_model.CANCELLABLE_ACTIONS["change_internal_port"]
        text = i18n.t(key, config, detail="build cache kept")
        assert "Start" in text, "the message must name the next step, not just report the abort"
        assert "build cache kept" in text


_FRONTENDS = [
    ("tk_window", "LauncherApp", None),
    ("ctk_window", "CtkLauncherApp", "HAS_CTK"),
    ("qt_window", "QtLauncherApp", "HAS_QT"),
]


class TestEveryFrontendArmsTheSignal:
    """The map alone shows a BUTTON; the signal is what makes it work.

    ``_apply_internal_port`` bypasses the shared ``_on_action`` worker (it needs
    the port's name), so each frontend has its own call site - exactly the drift
    the parity suites exist for. Checked at the source, because the alternative
    (three real windows) needs a display and would skip silently on the very
    machines this must hold for.
    """

    @pytest.mark.parametrize(("module_name", "class_name", "guard"), _FRONTENDS)
    def test_the_rebuild_is_started_cancellable(self, module_name: str, class_name: str, guard: str | None) -> None:
        module = importlib.import_module(f"docker_app_launcher.frontends.{module_name}")
        if guard is not None and not getattr(module, guard):
            pytest.skip(f"{module_name}: toolkit not installed")
        source = inspect.getsource(getattr(module, class_name)._apply_internal_port)
        assert "_cancel_build.clear()" in source, f"{module_name}: stale cancel signal would abort the rebuild at once"
        assert "should_cancel=" in source, f"{module_name}: the rebuild runs without the cancel predicate"
        assert '_show_cancel_for("change_internal_port")' in source, f"{module_name}: no visible way to cancel"
