"""Tests for the pure GUI helpers - no Tk window is created."""

from __future__ import annotations

import tkinter as tk
from typing import Any

import pytest

from docker_app_launcher import actions, gui
from docker_app_launcher.config import LauncherConfig


@pytest.fixture
def cfg() -> LauncherConfig:
    return LauncherConfig(app_name="X").resolve()


class TestPortEditable:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("not_installed", True),
            ("stopped", True),
            ("running", True),
            ("no_docker", False),
        ],
    )
    def test_states(self, state: str, expected: bool) -> None:
        assert gui.port_editable(state) is expected


class TestButtonStates:
    """Every button is always visible; only its enabled flag changes per state."""

    def test_no_docker_disables_everything_except_info(self) -> None:
        # "info" stays clickable everywhere - bug reports happen exactly when
        # Docker is down (#30).
        for name in gui.PRIMARY_BUTTONS + gui.SECONDARY_BUTTONS:
            expected = name == "info"
            assert gui.button_enabled("no_docker", name) is expected

    def test_not_installed(self) -> None:
        for name in ("install", "copy_log", "cleanup"):
            assert gui.button_enabled("not_installed", name) is True
        for name in ("start", "stop", "uninstall", "open_browser", "apply_port", "background"):
            assert gui.button_enabled("not_installed", name) is False

    def test_stopped(self) -> None:
        for name in ("start", "uninstall", "apply_port", "copy_log", "cleanup"):
            assert gui.button_enabled("stopped", name) is True
        for name in ("install", "stop", "open_browser", "background"):
            assert gui.button_enabled("stopped", name) is False

    def test_running(self) -> None:
        for name in ("open_browser", "stop", "uninstall", "apply_port", "copy_log", "cleanup", "background"):
            assert gui.button_enabled("running", name) is True
        for name in ("install", "start"):
            assert gui.button_enabled("running", name) is False

    def test_unknown_state_all_disabled(self) -> None:
        assert gui.button_enabled("weird", "install") is False

    def test_apply_port_and_copy_log_are_primary(self) -> None:
        assert "apply_port" in gui.PRIMARY_BUTTONS
        assert "copy_log" in gui.PRIMARY_BUTTONS

    def test_secondary_is_cleanup_logs_background_info(self) -> None:
        assert gui.SECONDARY_BUTTONS == ["cleanup", "app_logs", "background", "info"]

    def test_app_logs_enabled_only_when_containers_exist(self) -> None:
        assert gui.button_enabled("running", "app_logs") is True
        assert gui.button_enabled("stopped", "app_logs") is True  # crashed container's last words
        assert gui.button_enabled("not_installed", "app_logs") is False
        assert gui.button_enabled("no_docker", "app_logs") is False

    def test_app_logs_dispatches_to_actions(self, monkeypatch) -> None:
        from docker_app_launcher import actions, ui_model

        monkeypatch.setattr(actions, "app_logs", lambda cfg: (True, "web-1 | ready"))
        cfg = LauncherConfig(app_name="X").resolve()
        assert ui_model.dispatch_action("app_logs", cfg) == (True, "web-1 | ready")


class TestDisabledReason:
    def test_enabled_button_has_no_reason(self) -> None:
        assert gui.disabled_reason_key("install", "not_installed") == ""

    def test_no_docker_needs_docker(self) -> None:
        assert gui.disabled_reason_key("install", "no_docker") == "tooltip_needs_docker"

    def test_install_already_installed(self) -> None:
        assert gui.disabled_reason_key("install", "running") == "tooltip_already_installed"

    def test_start_already_running_vs_not_installed(self) -> None:
        assert gui.disabled_reason_key("start", "running") == "tooltip_already_running"
        assert gui.disabled_reason_key("start", "not_installed") == "tooltip_not_installed"

    def test_stop_not_running_when_stopped(self) -> None:
        assert gui.disabled_reason_key("stop", "stopped") == "tooltip_not_running"

    def test_background_only_running(self) -> None:
        assert gui.disabled_reason_key("background", "stopped") == "tooltip_only_running"

    def test_copy_log_no_log(self) -> None:
        assert gui.disabled_reason_key("copy_log", "no_docker") == "tooltip_no_log"

    def test_all_reason_keys_exist_in_every_locale(self) -> None:
        from docker_app_launcher import i18n

        reasons = {
            gui.disabled_reason_key(name, state)
            for state in ("no_docker", "not_installed", "stopped", "running")
            for name in gui.PRIMARY_BUTTONS + gui.SECONDARY_BUTTONS
        }
        reasons.discard("")
        for lang in i18n.available_languages():
            for key in reasons:
                assert key in i18n.STRINGS[lang], f"{key} missing in {lang}"


class TestDispatchAction:
    def test_install_routes_to_ensure_installed(self, cfg, monkeypatch) -> None:
        called: dict[str, object] = {}
        monkeypatch.setattr(actions, "ensure_installed", lambda c, **k: called.setdefault("v", (True, "done")))
        assert gui.dispatch_action("install", cfg) == (True, "done")
        assert "v" in called

    def test_start_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "start", lambda c, **k: (True, "started"))
        assert gui.dispatch_action("start", cfg) == (True, "started")

    def test_install_forwards_should_cancel(self, cfg, monkeypatch) -> None:
        # The build-cancel signal (#60) must reach the actions layer for the
        # build-capable ids (install/start), so closing the window can stop it.
        seen: dict[str, object] = {}
        monkeypatch.setattr(actions, "ensure_installed", lambda c, **k: seen.update(k) or (True, "done"))
        flag = lambda: True  # noqa: E731 - a trivial stand-in callback
        gui.dispatch_action("install", cfg, should_cancel=flag)
        assert seen.get("should_cancel") is flag

    def test_start_forwards_should_cancel(self, cfg, monkeypatch) -> None:
        seen: dict[str, object] = {}
        monkeypatch.setattr(actions, "start", lambda c, **k: seen.update(k) or (True, "started"))
        flag = lambda: False  # noqa: E731 - a trivial stand-in callback
        gui.dispatch_action("start", cfg, should_cancel=flag)
        assert seen.get("should_cancel") is flag

    def test_update_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "update", lambda c, **k: (True, "updated"))
        assert gui.dispatch_action("update", cfg) == (True, "updated")

    def test_update_forwards_should_cancel(self, cfg, monkeypatch) -> None:
        # Update rebuilds/re-pulls via start(), so the build-cancel signal (#60)
        # must reach the actions layer here too.
        seen: dict[str, object] = {}
        monkeypatch.setattr(actions, "update", lambda c, **k: seen.update(k) or (True, "updated"))
        flag = lambda: True  # noqa: E731 - a trivial stand-in callback
        gui.dispatch_action("update", cfg, should_cancel=flag)
        assert seen.get("should_cancel") is flag

    def test_stop_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "stop", lambda c: (True, "stopped"))
        assert gui.dispatch_action("stop", cfg) == (True, "stopped")

    def test_uninstall_routes(self, cfg, monkeypatch) -> None:
        monkeypatch.setattr(actions, "uninstall", lambda c, **k: (True, "gone"))
        assert gui.dispatch_action("uninstall", cfg) == (True, "gone")

    def test_change_port_routes_with_port(self, cfg, monkeypatch) -> None:
        seen: dict[str, object] = {}

        def fake_change(c, p, **k):
            seen["port"] = p
            return (True, "ok")

        monkeypatch.setattr(actions, "change_port", fake_change)
        assert gui.dispatch_action("change_port", cfg, port=9000) == (True, "ok")
        assert seen["port"] == 9000

    def test_change_port_without_port_is_invalid(self, cfg) -> None:
        result = gui.dispatch_action("change_port", cfg)
        assert result is not None
        ok, msg = result
        assert ok is False and "between" in msg

    def test_open_returns_none(self, cfg, monkeypatch) -> None:
        opened: list[object] = []
        monkeypatch.setattr(actions, "open_browser", lambda c: opened.append(c))
        assert gui.dispatch_action("open", cfg) is None
        assert opened == [cfg]

    def test_recheck_returns_none(self, cfg) -> None:
        assert gui.dispatch_action("recheck", cfg) is None

    def test_unknown_returns_none(self, cfg) -> None:
        assert gui.dispatch_action("frobnicate", cfg) is None


class _FakeButton:
    """Stands in for a ``tk.Button``: supports ``btn["state"] = ...``."""

    def __init__(self) -> None:
        self.state = "normal"

    def __setitem__(self, key: str, value: str) -> None:
        assert key == "state"
        self.state = value


def _busy_app(monkeypatch: pytest.MonkeyPatch, buttons: list[_FakeButton]) -> tuple[gui.LauncherApp, dict[str, Any]]:
    """Build a LauncherApp without a real Tk window, with every Tk-touching
    method stubbed so ``_set_busy`` can be exercised headlessly."""
    app = gui.LauncherApp.__new__(gui.LauncherApp)
    app._cfg = LauncherConfig(app_name="X").resolve()
    calls: dict[str, Any] = {"attributes": [], "lift": 0, "focus_force": 0, "logged": 0, "cleared": 0}
    # ``_iter_buttons`` would walk a real widget tree; feed it our fakes instead
    # so the test does not need a window. The real ``_set_topmost`` /
    # ``_bring_to_front`` still run, calling the stubbed primitives below.
    monkeypatch.setattr(app, "_iter_buttons", lambda: buttons)
    monkeypatch.setattr(app, "attributes", lambda *a: calls["attributes"].append(a))
    monkeypatch.setattr(app, "lift", lambda: calls.__setitem__("lift", calls["lift"] + 1))
    monkeypatch.setattr(app, "focus_force", lambda: calls.__setitem__("focus_force", calls["focus_force"] + 1))
    monkeypatch.setattr(app, "deiconify", lambda: None, raising=False)
    monkeypatch.setattr(app, "_clear_status", lambda: calls.__setitem__("cleared", calls["cleared"] + 1))
    monkeypatch.setattr(app, "_log", lambda *a, **k: calls.__setitem__("logged", calls["logged"] + 1))
    return app, calls


class TestSetBusy:
    def test_all_buttons_disabled_during_action(self, monkeypatch) -> None:
        buttons = [_FakeButton(), _FakeButton(), _FakeButton()]
        app, _ = _busy_app(monkeypatch, buttons)
        app._set_busy(True)
        assert all(btn.state == "disabled" for btn in buttons)

    def test_all_buttons_enabled_after_action(self, monkeypatch) -> None:
        buttons = [_FakeButton(), _FakeButton()]
        app, _ = _busy_app(monkeypatch, buttons)
        app._set_busy(True)
        app._set_busy(False)
        assert all(btn.state == "normal" for btn in buttons)

    def test_topmost_set_while_busy(self, monkeypatch) -> None:
        app, calls = _busy_app(monkeypatch, [_FakeButton()])
        app._set_busy(True)
        assert ("-topmost", True) in calls["attributes"]
        # Busy must not steal focus repeatedly; front-raising happens on finish.
        assert calls["lift"] == 0 and calls["focus_force"] == 0

    def test_topmost_cleared_and_window_raised_after(self, monkeypatch) -> None:
        app, calls = _busy_app(monkeypatch, [_FakeButton()])
        app._set_busy(True)
        app._set_busy(False)
        assert calls["attributes"][-1] == ("-topmost", False)
        assert calls["lift"] == 1 and calls["focus_force"] == 1

    def test_busy_clears_and_logs_once(self, monkeypatch) -> None:
        app, calls = _busy_app(monkeypatch, [_FakeButton()])
        app._set_busy(True)
        assert calls["cleared"] == 1 and calls["logged"] == 1

    def test_topmost_tclerror_is_swallowed(self, monkeypatch) -> None:
        app, _ = _busy_app(monkeypatch, [_FakeButton()])

        def boom(*_a: object) -> None:
            raise tk.TclError("no WM")

        monkeypatch.setattr(app, "attributes", boom)
        # A window-manager quirk must never crash an action.
        app._set_busy(True)


class TestAdvancedPorts:
    def _cfg(self) -> LauncherConfig:
        return LauncherConfig(
            app_name="X",
            internal_ports={"backend": 8000, "nginx": 80},
            env_internal_port_keys={"backend": "APP_BACKEND_PORT", "nginx": "APP_NGINX_PORT"},
            show_advanced_ports=True,
        ).resolve()

    def test_visible_only_when_opted_in_and_declared(self) -> None:
        assert gui.advanced_ports_visible(self._cfg()) is True
        off = LauncherConfig(app_name="X", show_advanced_ports=False).resolve()
        assert gui.advanced_ports_visible(off) is False
        # opted in but nothing declared -> still hidden
        empty = LauncherConfig(app_name="X", show_advanced_ports=True).resolve()
        assert gui.advanced_ports_visible(empty) is False

    def test_internal_port_fields_rows(self) -> None:
        rows = gui.internal_port_fields(self._cfg())
        names = [name for name, _, _ in rows]
        assert names == ["backend", "nginx"]  # sorted
        values = {name: value for name, _, value in rows}
        assert values == {"backend": 8000, "nginx": 80}
        assert all(label for _, label, _ in rows)

    def test_default_internal_ports(self) -> None:
        assert gui.default_internal_ports(self._cfg()) == {"backend": 8000, "nginx": 80}


class TestShouldKeepAliveOnClose:
    def test_running_and_enabled_keeps_alive(self) -> None:
        assert gui.should_keep_alive_on_close("running", minimize_enabled=True) is True

    def test_running_but_disabled_quits(self) -> None:
        assert gui.should_keep_alive_on_close("running", minimize_enabled=False) is False

    def test_not_running_quits(self) -> None:
        assert gui.should_keep_alive_on_close("stopped", minimize_enabled=True) is False
        assert gui.should_keep_alive_on_close("not_installed", minimize_enabled=True) is False


class TestShouldMinimizeToTray:
    def test_running_with_tray(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=True, tray_enabled=True) is True

    def test_running_no_tray(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=False, tray_enabled=True) is False

    def test_running_tray_disabled(self) -> None:
        assert gui.should_minimize_to_tray("running", tray_available=True, tray_enabled=False) is False

    def test_stopped_never_minimizes(self) -> None:
        assert gui.should_minimize_to_tray("stopped", tray_available=True, tray_enabled=True) is False


class _StubText:
    """Stands in for the log ``tk.Text`` widget: only ``get`` is exercised."""

    def __init__(self, content: str) -> None:
        self._content = content

    def get(self, start: str, end: str) -> str:
        return self._content


class _StubCopyButton:
    """Stands in for the copy-log ``tk.Button``: records every label set."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def configure(self, *, text: str) -> None:
        self.texts.append(text)


def _copy_log_app(
    monkeypatch: pytest.MonkeyPatch, content: str
) -> tuple[gui.LauncherApp, _StubCopyButton, dict[str, Any]]:
    """Build a LauncherApp without a real Tk window, with the log widget,
    copy button, and clipboard primitives stubbed so ``_copy_log`` runs
    headlessly (same idiom as ``_busy_app``)."""
    app = gui.LauncherApp.__new__(gui.LauncherApp)
    app._cfg = LauncherConfig(app_name="X").resolve()
    btn = _StubCopyButton()
    calls: dict[str, Any] = {"cleared": 0, "appended": [], "scheduled": []}
    # ``_status`` / ``_copy_log_btn`` are created in ``__init__`` (skipped here),
    # so they are absent on the bare instance - assign directly (monkeypatch
    # would probe the missing attr and trip ``Tk.__getattr__`` recursion). The
    # clipboard / after primitives DO exist on ``tk.Misc`` and are monkeypatched.
    app._status = _StubText(content)  # type: ignore[assignment]
    app._copy_log_btn = btn  # type: ignore[assignment]
    monkeypatch.setattr(app, "clipboard_clear", lambda: calls.__setitem__("cleared", calls["cleared"] + 1))
    monkeypatch.setattr(app, "clipboard_append", lambda text: calls["appended"].append(text))
    monkeypatch.setattr(app, "after", lambda ms, cb: calls["scheduled"].append((ms, cb)))
    return app, btn, calls


class TestCopyLog:
    def test_copies_stripped_content_and_shows_feedback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, btn, calls = _copy_log_app(monkeypatch, "  line one\nline two\n")
        app._copy_log()
        assert calls["cleared"] == 1
        assert calls["appended"] == ["line one\nline two"]
        # feedback flips to the localized "copied" label ...
        assert btn.texts == [app._t("log_copied")]
        # ... and the scheduled restore callback flips it back after ~2s
        assert calls["scheduled"] and calls["scheduled"][0][0] == 2000
        calls["scheduled"][0][1]()
        assert btn.texts == [app._t("log_copied"), app._t("log_copy")]

    def test_empty_log_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, btn, calls = _copy_log_app(monkeypatch, "   \n  ")
        app._copy_log()
        assert calls["cleared"] == 0
        assert calls["appended"] == []
        assert btn.texts == []
        assert calls["scheduled"] == []

    def test_copy_log_keys_exist_in_every_locale(self) -> None:
        from docker_app_launcher import i18n

        for lang in i18n.available_languages():
            assert "log_copy" in i18n.STRINGS[lang]
            assert "log_copied" in i18n.STRINGS[lang]


class _ImmediateThread:
    """Runs the target synchronously so cleanup-scan tests stay deterministic."""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        if self._target is not None:
            self._target()


def _cleanup_app(monkeypatch: pytest.MonkeyPatch) -> tuple[gui.LauncherApp, dict[str, Any]]:
    """LauncherApp without a Tk window, with the scan thread + Tk marshaling +
    log + offer stubbed so ``_run_manual_cleanup`` runs synchronously."""
    app = gui.LauncherApp.__new__(gui.LauncherApp)
    app._cfg = LauncherConfig(app_name="X").resolve()
    calls: dict[str, Any] = {"logged": [], "offered": []}
    monkeypatch.setattr("docker_app_launcher.frontends.tk_window.threading.Thread", _ImmediateThread)
    monkeypatch.setattr(app, "after", lambda ms, fn: fn())
    monkeypatch.setattr(app, "_log", lambda msg, **kw: calls["logged"].append(msg))
    monkeypatch.setattr(app, "_show_cleanup_offer", lambda stale: calls["offered"].append(stale))
    return app, calls


class TestManualCleanup:
    def test_shows_offer_when_artifacts_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _cleanup_app(monkeypatch)
        stale = {"volumes": ["x_data"]}
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda cfg: stale)
        monkeypatch.setattr(actions, "has_stale_artifacts", lambda s: True)
        app._run_manual_cleanup()
        assert calls["offered"] == [stale]

    def test_reports_nothing_when_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _cleanup_app(monkeypatch)
        monkeypatch.setattr(actions, "find_stale_artifacts", lambda cfg: {})
        monkeypatch.setattr(actions, "has_stale_artifacts", lambda s: False)
        app._run_manual_cleanup()
        assert calls["offered"] == []
        assert app._t("cleanup_none") in calls["logged"]

    def test_scan_error_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _cleanup_app(monkeypatch)

        def boom(cfg):
            raise RuntimeError("docker down")

        monkeypatch.setattr(actions, "find_stale_artifacts", boom)
        app._run_manual_cleanup()  # must not raise
        assert calls["offered"] == []
        assert any("docker down" in str(m) for m in calls["logged"])


# --- version display + about info (#30) -------------------------------------


class TestWindowTitle:
    def test_title_contains_app_name_and_real_version(self) -> None:
        import docker_app_launcher
        from docker_app_launcher import ui_model

        cfg = LauncherConfig(app_name="My App").resolve()
        title = ui_model.window_title(cfg)
        assert title.startswith("My App")
        assert docker_app_launcher.__version__ in title

    def test_title_follows_the_version_source_not_a_hardcode(self, monkeypatch) -> None:
        from docker_app_launcher import ui_model

        monkeypatch.setattr(ui_model, "launcher_version", lambda: "9.9.9-test")
        cfg = LauncherConfig(app_name="My App").resolve()
        assert "9.9.9-test" in ui_model.window_title(cfg)


class TestAboutInfo:
    def test_about_lines_carry_version_platform_backend(self) -> None:
        import platform as _platform

        import docker_app_launcher
        from docker_app_launcher import ui_model

        cfg = LauncherConfig(app_name="X", gui_backend="qt").resolve()
        text = "\n".join(ui_model.about_lines(cfg))
        assert docker_app_launcher.__version__ in text
        assert _platform.system() in text
        assert "qt" in text

    def test_about_lines_show_active_docker_endpoint_override(self, monkeypatch) -> None:
        from docker_app_launcher import actions, ui_model

        monkeypatch.setattr(actions, "docker_host_override", lambda: "unix:///run/user/1000/docker.sock")
        cfg = LauncherConfig(app_name="X").resolve()
        assert any("unix:///run/user/1000/docker.sock" in line for line in ui_model.about_lines(cfg))

    def test_issue_tracker_url_from_repo_url(self) -> None:
        from docker_app_launcher import ui_model

        cfg = LauncherConfig(app_name="X", repo_url="https://github.com/owner/myapp").resolve()
        assert ui_model.issue_tracker_url(cfg) == "https://github.com/owner/myapp/issues"

    def test_issue_tracker_url_defaults_to_launcher_repo(self) -> None:
        from docker_app_launcher import ui_model

        cfg = LauncherConfig(app_name="X").resolve()
        assert ui_model.issue_tracker_url(cfg).endswith("/issues")


class TestInfoButtonModel:
    def test_info_is_a_secondary_button(self) -> None:
        assert "info" in gui.SECONDARY_BUTTONS

    def test_info_enabled_in_every_state_including_no_docker(self) -> None:
        for state in ("no_docker", "not_installed", "stopped", "running"):
            assert gui.button_enabled(state, "info") is True, f"info must stay clickable in {state}"


class TestAboutAppVersion:
    """#35: about_lines label launcher vs actually-running app version."""

    def test_about_lines_show_running_app_version(self, monkeypatch) -> None:
        from docker_app_launcher import actions, ui_model

        monkeypatch.setattr(actions, "get_app_version", lambda cfg: ("2.6.0", "running"))
        cfg = LauncherConfig(app_name="X", app_version="9.9.9").resolve()
        text = "\n".join(ui_model.about_lines(cfg))
        assert "App: X 2.6.0 (running)" in text
        assert "9.9.9" not in text
        assert any(line.startswith("Launcher: docker-app-launcher v") for line in text.splitlines())

    def test_about_lines_fail_open_without_any_version(self, monkeypatch) -> None:
        from docker_app_launcher import actions, ui_model

        monkeypatch.setattr(actions, "get_app_version", lambda cfg: ("", "unknown"))
        cfg = LauncherConfig(app_name="X", app_version="").resolve()
        text = "\n".join(ui_model.about_lines(cfg))
        assert "App: X" in text
        assert "(unknown)" not in text


class TestLogPanelMirror:
    """P0: every log-panel line must also reach the logging system, so the
    persistent launcher.log carries what the user saw on screen."""

    def test_info_line_logged_at_info(self, caplog) -> None:
        from docker_app_launcher import ui_model

        with caplog.at_level("INFO", logger="docker_app_launcher.ui.panel"):
            ui_model.log_panel_line("Docker is running.")
        assert caplog.records[0].levelname == "INFO"
        assert "Docker is running." in caplog.records[0].message

    def test_err_line_logged_at_error(self, caplog) -> None:
        from docker_app_launcher import ui_model

        with caplog.at_level("INFO", logger="docker_app_launcher.ui.panel"):
            ui_model.log_panel_line("build failed", "err")
        assert caplog.records[0].levelname == "ERROR"

    def test_ok_and_unknown_tags_fall_back_to_info(self, caplog) -> None:
        from docker_app_launcher import ui_model

        with caplog.at_level("INFO", logger="docker_app_launcher.ui.panel"):
            ui_model.log_panel_line("done", "ok")
            ui_model.log_panel_line("odd", "sparkly")
        assert [r.levelname for r in caplog.records] == ["INFO", "INFO"]


class TestRunGuarded:
    """P1: a crashing worker body becomes an ordinary failed result."""

    def test_passes_result_through(self) -> None:
        from docker_app_launcher import ui_model

        assert ui_model.run_guarded("x", lambda: (True, "fine")) == (True, "fine")

    def test_none_result_passes_through(self) -> None:
        from docker_app_launcher import ui_model

        assert ui_model.run_guarded("open", lambda: None) is None

    def test_exception_becomes_failed_result(self, caplog) -> None:
        from docker_app_launcher import ui_model

        def boom() -> tuple[bool, str]:
            raise ValueError("port table corrupt")

        with caplog.at_level("ERROR", logger="docker_app_launcher.ui_model"):
            result = ui_model.run_guarded("change_port", boom)
        assert result is not None
        ok, msg = result
        assert ok is False
        assert "ValueError" in msg and "port table corrupt" in msg
        assert any(r.exc_info and "change_port" in r.message for r in caplog.records)


class TestInitialFocus:
    """#31: keyboard focus lands on the state's primary next action."""

    def test_mapping(self) -> None:
        from docker_app_launcher import ui_model

        assert ui_model.initial_focus_button("not_installed") == "install"
        assert ui_model.initial_focus_button("stopped") == "start"
        assert ui_model.initial_focus_button("running") == "open_browser"
        assert ui_model.initial_focus_button("no_docker") == "info"

    def test_unknown_state_falls_back_to_info(self) -> None:
        from docker_app_launcher import ui_model

        assert ui_model.initial_focus_button("weird") == "info"

    def test_target_is_always_enabled_in_its_state(self) -> None:
        from docker_app_launcher import ui_model

        for state in ("no_docker", "not_installed", "stopped", "running"):
            target = ui_model.initial_focus_button(state)
            assert ui_model.button_enabled(state, target), f"{state}: focus target {target!r} is disabled"
