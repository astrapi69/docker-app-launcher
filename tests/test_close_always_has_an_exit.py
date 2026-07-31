"""Closing the window must always have an exit (#108).

Device finding (Ubuntu + Windows, frozen wrapper): the launcher could not
be closed at all - only the task manager ended it. Cause: the X button
keeps a RUNNING app alive and sends the window to the background, but the
only Quit control lives in the TRAY MENU. Without a tray (frozen artifact
by design, or a source install without the ``tray`` extra) the window
iconifies to the taskbar, comes back on click, iconifies again - and the
quit path does not exist anywhere in the UI.

The keep-alive decision therefore must not be made without knowing that
an exit will exist. This is the #103 class in its harshest form: a
convenience (keep the running app reachable) that removes the ONLY way
out. Quitting the launcher is safe - the app runs in Docker and keeps
running; the launcher is a control window, not the app's host process.

Coverage here:
- the decision itself, over the full state x tray x opt-in matrix,
- the three frontends' close handlers pass the tray fact (sync pin, so a
  frontend cannot regress into the trap on its own),
- a real Tk window proves the X quits when no tray can dock.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import socket

import pytest

from docker_app_launcher import gui, ui_model
from docker_app_launcher.config import LauncherConfig

FRONTEND_MODULES = (
    "docker_app_launcher.frontends.tk_window",
    "docker_app_launcher.frontends.ctk_window",
    "docker_app_launcher.frontends.qt_window",
)


class TestKeepAliveNeedsAnExit:
    """RED before the fix: keep-alive was True with no tray anywhere."""

    def test_running_without_tray_does_not_keep_the_window_alive(self) -> None:
        assert ui_model.should_keep_alive_on_close("running", minimize_enabled=True, tray_available=False) is False, (
            "no tray means no Quit control - the X must close the launcher instead of trapping the user"
        )

    def test_running_with_tray_keeps_the_window_alive(self) -> None:
        assert ui_model.should_keep_alive_on_close("running", minimize_enabled=True, tray_available=True) is True

    def test_default_is_the_safe_side(self) -> None:
        """A caller that does not know about trays must get the exit, not the trap."""
        assert ui_model.should_keep_alive_on_close("running", minimize_enabled=True) is False

    @pytest.mark.parametrize("state", ["stopped", "not_installed", "unknown"])
    def test_non_running_states_always_close(self, state: str) -> None:
        assert ui_model.should_keep_alive_on_close(state, minimize_enabled=True, tray_available=True) is False

    def test_opt_out_always_closes(self) -> None:
        assert ui_model.should_keep_alive_on_close("running", minimize_enabled=False, tray_available=True) is False

    def test_the_facade_exports_the_same_function(self) -> None:
        assert gui.should_keep_alive_on_close is ui_model.should_keep_alive_on_close


class TestEveryFrontendPassesTheTrayFact:
    """Sync pin: the trap must be unreachable in all three windows."""

    @pytest.mark.parametrize("module_name", FRONTEND_MODULES)
    def test_close_handler_passes_tray_available(self, module_name: str) -> None:
        module = __import__(module_name, fromlist=["*"])
        source = inspect.getsource(module)
        assert "should_keep_alive_on_close(" in source, f"{module_name} does not use the shared close decision"
        call_start = source.index("should_keep_alive_on_close(")
        call = source[call_start : source.index(")", source.index("minimize_enabled", call_start))]
        assert "tray_available=" in call, (
            f"{module_name} decides keep-alive without the tray fact - "
            f"that is the #108 trap (X minimizes forever, Quit lives only in the tray menu)"
        )


class TestQtExplicitQuitIsNotReJudged:
    """Second finding (#108, Qt only): the tray's Quit entry never quit.

    Qt's ``_quit`` routes through ``close()``, which re-enters ``closeEvent``.
    With a docked tray and a RUNNING app the keep-alive verdict there is True,
    so the window backgrounded itself instead of quitting - the tray menu's
    only exit was a no-op. RED before the fix: no ``_quitting`` guard existed.
    """

    def test_close_event_honors_an_explicit_quit(self) -> None:
        import docker_app_launcher.frontends.qt_window as qt_window

        source = inspect.getsource(qt_window)
        close_event = source[source.index("def closeEvent") : source.index("def _background_controller")]
        assert "self._quitting" in close_event, (
            "closeEvent re-judges an explicit quit: with a tray docked and the app "
            "running it would background instead of ending the process"
        )
        quit_method = source[source.index("def _quit(self)") :]
        quit_method = quit_method[: quit_method.index("def _confirm_quit_during_operation")]
        assert "self._quitting = True" in quit_method, "_quit does not mark the exit as explicit"

    def test_the_other_frontends_need_no_such_flag(self) -> None:
        """tk/ctk destroy() directly - no re-entry, so no flag to keep in sync."""
        for module_name in ("docker_app_launcher.frontends.tk_window", "docker_app_launcher.frontends.ctk_window"):
            module = __import__(module_name, fromlist=["*"])
            assert "self.destroy()" in inspect.getsource(module)


class TestEveryConditionHasAWayOut:
    """The class, not the single bug: enumerate the exits, prove each condition.

    Reports WHAT it measured (point 4 of the contract): the conditions and the
    paths are named in the assertion messages, so an unchecked combination
    cannot read as a covered one.
    """

    def test_every_condition_is_covered_by_at_least_one_exit(self) -> None:
        uncovered = [c for c in ui_model.EXIT_CONDITIONS if not ui_model.exit_paths_for(c)]
        assert not uncovered, (
            f"checked {len(ui_model.EXIT_CONDITIONS)} conditions "
            f"({', '.join(ui_model.EXIT_CONDITIONS)}) against "
            f"{len(ui_model.EXIT_PATHS)} exit paths ({', '.join(ui_model.EXIT_PATHS)}) - "
            f"no way out under: {uncovered}"
        )

    def test_the_close_policy_matches_the_enumeration(self) -> None:
        """The X must quit exactly under the condition where it is the exit."""
        for condition in ui_model.EXIT_CONDITIONS:
            tray_available = condition == "tray_available"
            keeps_alive = ui_model.should_keep_alive_on_close(
                "running", minimize_enabled=True, tray_available=tray_available
            )
            x_quits = not keeps_alive
            declared = "window_close" in ui_model.exit_paths_for(condition)
            assert x_quits == declared, (
                f"under {condition!r} EXIT_PATHS says window_close "
                f"{'is' if declared else 'is not'} an exit, but the close policy "
                f"{'quits' if x_quits else 'backgrounds'}"
            )

    def test_the_tray_condition_really_carries_a_quit_entry(self) -> None:
        """With a tray the X only backgrounds - so the tray menu MUST quit."""
        from docker_app_launcher import tray

        assert "tray_menu_quit" in ui_model.exit_paths_for("tray_available")
        assert "quit" in tray.menu_action_ids(), (
            "the tray menu lost its Quit entry - with a docked tray that is the only exit"
        )

    def test_an_unknown_condition_is_an_error_not_an_empty_set(self) -> None:
        """Fails closed: 'could not check' must never read as 'nothing to find'."""
        with pytest.raises(ValueError):
            ui_model.exit_paths_for("some_new_desktop_environment")

    def test_paths_only_declare_known_conditions(self) -> None:
        for path, conditions in ui_model.EXIT_PATHS.items():
            unknown = set(conditions) - set(ui_model.EXIT_CONDITIONS)
            assert not unknown, f"exit path {path!r} declares unknown conditions: {sorted(unknown)}"


class TestQuitDuringOperation:
    """Part 3: quitting ENDS a running operation, so it asks and says so."""

    def test_the_message_names_the_action_and_the_resulting_state(self) -> None:
        from docker_app_launcher import i18n

        cfg = LauncherConfig(
            app_name="Msg", container_name="msg", image_name="msg:test", compose_file="docker-compose.yml"
        )
        for locale in ("en", "de"):
            cfg.locale = locale
            text = i18n.t("quit_during_operation", cfg, action="Install")
            assert "Install" in text
            assert "?" in text, "it is a question - the user decides"

    def test_every_catalog_carries_the_key(self) -> None:
        from pathlib import Path

        import yaml

        catalogs = sorted(Path("src/docker_app_launcher/i18n").glob("*.yaml"))
        assert len(catalogs) == 11, f"expected 11 catalogs, found {len(catalogs)}"
        missing = [
            c.stem for c in catalogs if "quit_during_operation" not in yaml.safe_load(c.read_text(encoding="utf-8"))
        ]
        assert not missing, f"checked {len(catalogs)} catalogs - quit_during_operation missing in: {missing}"

    @pytest.mark.parametrize("module_name", FRONTEND_MODULES)
    def test_every_frontend_confirms_before_it_ends_an_operation(self, module_name: str) -> None:
        module = __import__(module_name, fromlist=["*"])
        source = inspect.getsource(module)
        assert "quit_during_operation" in source, f"{module_name} quits without asking during an operation"
        assert "LONG_RUNNING_ACTIONS" in source, f"{module_name} does not scope the question to real operations"

    @pytest.mark.parametrize("module_name", FRONTEND_MODULES)
    def test_every_frontend_clears_the_in_flight_marker(self, module_name: str) -> None:
        """Else a finished operation would still trigger the question."""
        module = __import__(module_name, fromlist=["*"])
        assert "self._current_action = None" in inspect.getsource(module), (
            f"{module_name} never clears _current_action - a quit after a finished "
            f"operation would ask about a step that is long over"
        )


def _display_available() -> bool:
    if os.environ.get("DISPLAY"):
        return True
    try:
        with socket.create_connection(("127.0.0.1", 6099), timeout=0.3):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _display_available(), reason="needs a display (xvfb) for the real window")
class TestRealWindowClosesWithoutTray:
    @pytest.fixture
    def app(self):
        from docker_app_launcher.frontends.tk_window import LauncherApp

        cfg = LauncherConfig(
            app_name="CloseExit",
            container_name="close-exit",
            image_name="close-exit:test",
            compose_file="docker-compose.yml",
        )
        window = LauncherApp(cfg)
        window.update_idletasks()
        yield window
        with contextlib.suppress(Exception):  # the test may already have quit it
            window.destroy()

    def test_x_quits_when_no_tray_can_dock(self, app, monkeypatch) -> None:
        from docker_app_launcher import actions, tray

        monkeypatch.setattr(actions, "get_state", lambda _cfg: "running")
        monkeypatch.setattr(tray, "tray_available", lambda: False)
        quit_calls: list[bool] = []
        monkeypatch.setattr(app, "_quit", lambda: quit_calls.append(True))
        iconified: list[bool] = []
        monkeypatch.setattr(app, "iconify", lambda: iconified.append(True))

        app._on_close()

        assert quit_calls == [True], "without a tray the X must quit - otherwise only the task manager can"
        assert iconified == [], "the window must not go to the taskbar when there is no way back out"

    def test_quit_asks_while_an_operation_runs(self, app, monkeypatch) -> None:
        """Quitting ends the worker thread - the user must be asked first."""
        from tkinter import messagebox

        app._current_action = "install"
        asked: list[str] = []

        def _record(*args: object, **_kw: object) -> bool:
            asked.append(str(args[1]))
            return False

        monkeypatch.setattr(messagebox, "askyesno", _record)
        destroyed: list[bool] = []
        monkeypatch.setattr(app, "destroy", lambda: destroyed.append(True))

        app._quit()

        assert asked, "quitting during an operation must ask"
        assert "install" in asked[0].lower() or "install" in asked[0]
        assert destroyed == [], "declining the question must keep the window open"

    def test_quit_without_a_running_operation_does_not_ask(self, app, monkeypatch) -> None:
        from tkinter import messagebox

        app._current_action = None
        monkeypatch.setattr(messagebox, "askyesno", lambda *a, **kw: pytest.fail("nothing runs - do not ask"))
        destroyed: list[bool] = []
        monkeypatch.setattr(app, "destroy", lambda: destroyed.append(True))

        app._quit()

        assert destroyed == [True]

    def test_x_backgrounds_when_a_tray_is_available(self, app, monkeypatch) -> None:
        from docker_app_launcher import actions, tray

        monkeypatch.setattr(actions, "get_state", lambda _cfg: "running")
        monkeypatch.setattr(tray, "tray_available", lambda: True)
        background: list[bool] = []
        monkeypatch.setattr(app, "_go_background", lambda **_kw: background.append(True))
        monkeypatch.setattr(app, "_quit", lambda: pytest.fail("must not quit while a tray can hold the launcher"))

        app._on_close()

        assert background == [True]
