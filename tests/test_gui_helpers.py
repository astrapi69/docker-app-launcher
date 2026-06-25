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

    def test_no_docker_disables_everything(self) -> None:
        for name in gui.PRIMARY_BUTTONS + gui.SECONDARY_BUTTONS:
            assert gui.button_enabled("no_docker", name) is False

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

    def test_secondary_is_cleanup_then_background(self) -> None:
        assert gui.SECONDARY_BUTTONS == ["cleanup", "background"]


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
    monkeypatch.setattr("docker_app_launcher.gui.threading.Thread", _ImmediateThread)
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
