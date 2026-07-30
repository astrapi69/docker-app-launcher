"""The persistent launcher window.

ONE window. It opens, shows the current state, and NEVER closes itself - the
only way to close it is the window's X button. There is no dialog chain:
install / start / stop / uninstall / cleanup all happen in-place, streaming
their progress into the scrollable status area.

The Tk layer is intentionally thin. All behaviour lives in :mod:`actions`, and
the pure helpers below (:func:`port_editable`, :func:`button_enabled`,
:func:`disabled_reason_key`, :func:`dispatch_action`,
:func:`should_minimize_to_tray`) carry the decisions so they are unit-testable
without a display.

Button model (state pattern): every button exists for the whole lifetime of the
window and is ALWAYS visible. A button is never hidden or removed - only
enabled or disabled per the current app state via :data:`BUTTON_STATES`, with a
tooltip on a disabled button explaining why. The primary actions sit in a
two-column grid above the log; the secondary actions (cleanup / background /
apply-port) sit below the log under a separator.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import platform
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from docker_app_launcher import actions, i18n, lockfile, tray, ui_model, update_check
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig, locale_for_label
from docker_app_launcher.frontends.tooltip import Tooltip as _Tooltip

# The framework-neutral UI model lives in ``ui_model``; re-exported here so the
# long-standing ``gui.BUTTON_STATES`` / ``gui.dispatch_action`` API keeps
# working (tests and downstream code import them from this module).
from docker_app_launcher.ui_model import (
    _STATE_KEYS as _STATE_KEYS,
)
from docker_app_launcher.ui_model import (
    BUTTON_LABELS as BUTTON_LABELS,
)
from docker_app_launcher.ui_model import (
    BUTTON_STATES as BUTTON_STATES,
)
from docker_app_launcher.ui_model import (
    PRIMARY_BUTTONS as PRIMARY_BUTTONS,
)
from docker_app_launcher.ui_model import (
    PRIMARY_GRID as PRIMARY_GRID,
)
from docker_app_launcher.ui_model import (
    SECONDARY_BUTTONS as SECONDARY_BUTTONS,
)
from docker_app_launcher.ui_model import (
    about_lines as about_lines,
)
from docker_app_launcher.ui_model import (
    advanced_ports_visible as advanced_ports_visible,
)
from docker_app_launcher.ui_model import (
    button_enabled as button_enabled,
)
from docker_app_launcher.ui_model import (
    default_internal_ports as default_internal_ports,
)
from docker_app_launcher.ui_model import (
    disabled_reason_key as disabled_reason_key,
)
from docker_app_launcher.ui_model import (
    dispatch_action as dispatch_action,
)
from docker_app_launcher.ui_model import (
    initial_focus_button as initial_focus_button,
)
from docker_app_launcher.ui_model import (
    internal_port_fields as internal_port_fields,
)
from docker_app_launcher.ui_model import (
    issue_tracker_url as issue_tracker_url,
)
from docker_app_launcher.ui_model import (
    log_panel_line as log_panel_line,
)
from docker_app_launcher.ui_model import (
    port_editable as port_editable,
)
from docker_app_launcher.ui_model import (
    run_guarded as run_guarded,
)
from docker_app_launcher.ui_model import (
    should_keep_alive_on_close as should_keep_alive_on_close,
)
from docker_app_launcher.ui_model import (
    should_minimize_to_tray as should_minimize_to_tray,
)
from docker_app_launcher.ui_model import (
    window_title as window_title,
)

if TYPE_CHECKING:
    from docker_app_launcher.diagnostics_report import DoctorReport

logger = logging.getLogger("docker_app_launcher.gui")

# The assistant elements this window renders (#81): element -> builder
# method on LauncherApp. Pinned against ui_model.ASSISTANT_ELEMENTS by
# tests/test_frontend_parity.py - identical for every frontend.
ASSISTANT_WIDGET_BUILDERS = {
    "status_headline": "_build_status_headline",
    "doctor_checklist": "_build_doctor_checklist",
    "problem_card": "_build_problem_card",
    "copy_diagnosis_button": "_build_copy_diagnosis_button",
    "copy_support_bundle_button": "_build_copy_support_bundle_button",
    "log_toggle": "_build_log_toggle",
    "update_button": "_build_update_button",
}


class LauncherApp(tk.Tk):
    """The persistent window. Thin Tk over the helpers above."""

    def __init__(self, config: LauncherConfig, *, debug: bool = False) -> None:
        super().__init__()
        config.resolve()
        self._cfg = config
        # Effective UI language: the user's persisted picker choice wins over the
        # config default (which already resolved "auto" -> system locale).
        self._cfg.locale = actions.resolve_locale(self._cfg)
        self._debug = debug
        self._tray: tray.TrayController | None = None
        # Cancel signal for an in-progress build: set by closing the window
        # mid-build so the build subprocess is terminated, not orphaned (#60).
        self._cancel_build = threading.Event()
        self._build_in_progress = False
        self._buttons: dict[str, tk.Button] = {}
        self._log_follow_stop: threading.Event | None = None
        self._tooltips: dict[str, _Tooltip] = {}

        self.title(window_title(config))
        self.geometry(f"{config.window_width}x{config.window_height}")
        stored_geometry = actions.resolve_window_geometry(config)
        if stored_geometry:
            self.geometry(stored_geometry)
        if not config.window_resizable:
            self.resizable(False, False)
        self.minsize(min(600, config.window_width), min(420, config.window_height))
        _set_window_icon(self, config.icon_path)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # wraplength keeps the wordiest state text (docker_no_permission +
        # usermod command, #47) inside the window; the <Configure> binding
        # re-wraps it while the user resizes.
        self._state_label = tk.Label(self, font=("Segoe UI", 12, "bold"), wraplength=config.window_width - 40)
        self._state_label.pack(pady=(18, 8))
        self.bind("<Configure>", self._on_window_configure)

        port_row = tk.Frame(self)
        port_row.pack(pady=(0, 8))
        tk.Label(port_row, text="Port:").pack(side="left", padx=(0, 6))
        self._port_var = tk.StringVar(value=str(actions.resolve_port(config)))
        self._port_entry = tk.Entry(port_row, textvariable=self._port_var, width=8)
        self._port_entry.pack(side="left")
        self._port_indicator = tk.Label(port_row, text="", width=2)
        self._port_indicator.pack(side="left", padx=(6, 0))
        self._port_entry.bind("<KeyRelease>", lambda _e: self._validate_port())

        lang_row = tk.Frame(self)
        lang_row.pack(pady=(0, 6))
        tk.Label(lang_row, text="🌐").pack(side="left", padx=(0, 6))
        self._locale_var = tk.StringVar(value=LOCALE_LABELS.get(self._cfg.locale, self._cfg.locale))
        locale_combo = ttk.Combobox(
            lang_row,
            textvariable=self._locale_var,
            values=list(LOCALE_LABELS.values()),
            state="readonly",
            width=18,
        )
        locale_combo.pack(side="left")
        locale_combo.bind("<<ComboboxSelected>>", self._on_locale_change)

        self._internal_vars: dict[str, tk.StringVar] = {}
        if advanced_ports_visible(config):
            self._build_advanced_section()

        button_handlers = self._build_button_handlers()

        # Docker-help panel: shown only in the no-docker state (packed before the
        # primary grid). Empty otherwise.
        self._docker_help_frame = tk.Frame(self)

        # Primary actions: a fixed two-column grid, always visible. Enabled /
        # disabled per state in ``_update_button_states``.
        self._primary_frame = tk.Frame(self)
        self._primary_frame.pack(pady=(6, 0))
        for name in PRIMARY_BUTTONS:
            row, column = PRIMARY_GRID[name]
            self._make_button(self._primary_frame, name, button_handlers[name]).grid(
                row=row, column=column, padx=4, pady=2
            )
        # The copy-log button keeps a named alias for the "Copied!" feedback flip.
        self._copy_log_btn = self._buttons["copy_log"]

        # Progress bar + label above the log: a quick visual for long actions
        # (install/start build, cleanup), with the scrollable log below for
        # detail. Hidden until an action reports progress.
        self._progress_frame = tk.Frame(self)
        self._progress = ttk.Progressbar(self._progress_frame, mode="determinate", maximum=100)
        self._progress.pack(fill="x", padx=12, pady=(6, 0))
        self._progress_label = tk.Label(self._progress_frame, text="", anchor="w", font=("Segoe UI", 8))
        self._progress_label.pack(fill="x", padx=12)

        status_frame = tk.Frame(self)
        self._status_frame = status_frame
        status_frame.pack(fill="both", expand=True, padx=12, pady=(8, 8))
        scrollbar = tk.Scrollbar(status_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self._status = tk.Text(
            status_frame,
            height=8,
            wrap="word",
            state="disabled",
            relief="flat",
            font=("Consolas", 9),
            yscrollcommand=scrollbar.set,
        )
        self._status.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._status.yview)
        self._status.tag_configure("ok", foreground="#188038")
        self._status.tag_configure("err", foreground="#c5221f")
        self._status.tag_configure("info", foreground="#555")

        # Separator + secondary actions BELOW the log (packed after the expanding
        # log frame, so they sit at the bottom of the window). The progress bar
        # slots in between the log and the separator on demand.
        self._divider = ttk.Separator(self, orient="horizontal")
        self._divider.pack(fill="x", padx=12)
        self._secondary_frame = tk.Frame(self)
        self._secondary_frame.pack(pady=(6, 10))
        for name in SECONDARY_BUTTONS:
            self._make_button(self._secondary_frame, name, button_handlers[name]).pack(side="left", padx=4)

        # Installation assistant (#81): every element from
        # ui_model.ASSISTANT_ELEMENTS, built via ASSISTANT_WIDGET_BUILDERS.
        self._assistant_labels = ui_model.assistant_labels(config)
        self._assistant: dict[str, tk.Widget] = {}
        for element, builder in ASSISTANT_WIDGET_BUILDERS.items():
            self._assistant[element] = getattr(self, builder)()

        self._log(f"{about_lines(config)[0]} · {config.gui_backend} · {platform.system()}")
        self._refresh()
        if config.cleanup_on_start:
            self._offer_cleanup_if_stale()
        if config.update_check_enabled:
            self._check_for_update()
        if config.single_instance:
            # A refused second launch drops a focus marker (#31); poll it so
            # the running window comes to the foreground.
            self.after(1000, self._poll_focus_request)

    # --- button construction ---

    def _build_button_handlers(self) -> dict[str, Callable[[], None]]:
        """Map each button name to its click handler.

        ``copy_log`` / ``cleanup`` / ``background`` have bespoke handlers; the
        rest dispatch a normal action (``open_browser`` -> ``open``,
        ``apply_port`` -> ``change_port``).
        """
        return {
            "install": functools.partial(self._on_action, "install"),
            "start": functools.partial(self._on_action, "start"),
            "open_browser": functools.partial(self._on_action, "open"),
            "stop": functools.partial(self._on_action, "stop"),
            "uninstall": self._confirm_uninstall,
            "copy_log": self._copy_log,
            "cleanup": self._run_manual_cleanup,
            "app_logs": self._on_app_logs,
            "background": self._go_background,
            "apply_port": functools.partial(self._on_action, "change_port"),
            "info": self._show_about,
        }

    def _make_button(self, parent: tk.Frame, name: str, command: Callable[[], None]) -> tk.Button:
        """Create one always-visible button and register it + its tooltip.

        The caller places the returned button (``.grid`` for the primary grid,
        ``.pack`` for the secondary row).
        """
        # Explicit focus ring (#31): the default hairline is easy to miss.
        btn = tk.Button(
            parent,
            text=self._t(BUTTON_LABELS[name]),
            width=18,
            command=command,
            highlightthickness=2,
            highlightcolor="#2a5db0",
        )
        self._buttons[name] = btn
        self._tooltips[name] = _Tooltip(btn)
        return btn

    # --- helpers ---

    # --- installation assistant (#81) ---

    def _build_status_headline(self) -> tk.Widget:
        """The existing state heading IS the status head; severity styling is
        applied in _refresh via _apply_status_headline (symbol + color, never
        color alone)."""
        return self._state_label

    def _apply_status_headline(self, state: str, *, health_ok: bool | None = None) -> None:
        severity, text = ui_model.status_headline(self._cfg, state, health_ok=health_ok)
        colors = {"ok": "#188038", "error": "#c5221f", "info": "#333333"}
        self._state_label.configure(foreground=colors[severity])
        self._headline_symbol = text.split(" ", 1)[0]

    def _build_doctor_checklist(self) -> tk.Widget:
        """The 'Check system' button; results render as a checklist into the
        (auto-expanded) log panel and feed the problem card."""
        btn = tk.Button(
            self._secondary_frame,
            text=self._assistant_labels["system_check"],
            command=self._on_system_check,
        )
        btn.pack(side="left", padx=4)
        self._system_check_btn = btn
        return btn

    def _on_system_check(self) -> None:
        from docker_app_launcher.doctor import collect_doctor_report

        self._system_check_btn.configure(state="disabled")

        def _run() -> None:
            report = collect_doctor_report(self._cfg)
            self.after(0, lambda: self._render_doctor(report))

        threading.Thread(target=_run, daemon=True, name="dal-gui-doctor").start()

    def _render_doctor(self, report: DoctorReport) -> bool:
        self._system_check_btn.configure(state="normal")
        self._set_log_collapsed(False)
        for status, line in ui_model.doctor_checklist_rows(report):
            self._log(line, tag={"ok": "ok", "error": "err"}.get(status, "info"))
        card = ui_model.primary_problem(self._cfg, report)
        if card is None:
            self._log(self._assistant_labels["no_problems_found"], tag="ok")
            self._hide_problem_card()
        else:
            self._show_problem_card(card)
        return True

    def _build_problem_card(self) -> tk.Widget:
        """Problem class + 'What does this mean?' + 'What you can do' - shown
        on a failed system check, above the log; raw detail stays in the log."""
        frame = tk.Frame(self, relief="groove", borderwidth=1, padx=8, pady=6)
        wrap = max(200, self._cfg.window_width - 60)
        self._problem_title = tk.Label(frame, font=("Segoe UI", 10, "bold"), anchor="w", wraplength=wrap)
        self._problem_title.pack(fill="x")
        self._problem_message = tk.Label(frame, anchor="w", justify="left", wraplength=wrap)
        self._problem_message.pack(fill="x")
        self._problem_meaning_label = tk.Label(
            frame, text=self._assistant_labels["what_it_means"], font=("Segoe UI", 9, "bold"), anchor="w"
        )
        self._problem_meaning = tk.Label(frame, anchor="w", justify="left", wraplength=wrap)
        self._problem_fix_label = tk.Label(
            frame, text=self._assistant_labels["what_to_do"], font=("Segoe UI", 9, "bold"), anchor="w"
        )
        self._problem_fix = tk.Label(frame, anchor="w", justify="left", wraplength=wrap)
        self._problem_frame = frame
        return frame

    def _show_problem_card(self, card: dict[str, str]) -> None:
        self._problem_title.configure(text=f"✗ {card['title']}: {card['id']}")
        self._problem_message.configure(text=card["message"])
        for widget, text in (
            (self._problem_meaning_label, card["meaning_label"]),
            (self._problem_meaning, card["meaning"]),
            (self._problem_fix_label, card["fix_label"]),
            (self._problem_fix, card["fix"]),
        ):
            widget.configure(text=text)
            if text:
                widget.pack(fill="x")
            else:
                widget.pack_forget()
        self._problem_frame.pack(fill="x", padx=12, pady=(4, 0), before=self._status_frame)

    def _hide_problem_card(self) -> None:
        self._problem_frame.pack_forget()

    def _build_copy_diagnosis_button(self) -> tk.Widget:
        btn = tk.Button(
            self._secondary_frame,
            text=self._assistant_labels["copy_diagnosis"],
            command=lambda: self._copy_with_feedback("copy_diagnosis", ui_model.diagnosis_clipboard_text),
        )
        btn.pack(side="left", padx=4)
        self._copy_buttons = getattr(self, "_copy_buttons", {})
        self._copy_buttons["copy_diagnosis"] = btn
        return btn

    def _build_copy_support_bundle_button(self) -> tk.Widget:
        btn = tk.Button(
            self._secondary_frame,
            text=self._assistant_labels["copy_support_bundle"],
            command=lambda: self._copy_with_feedback("copy_support_bundle", ui_model.support_bundle_clipboard_text),
        )
        btn.pack(side="left", padx=4)
        self._copy_buttons["copy_support_bundle"] = btn
        return btn

    def _copy_with_feedback(self, label_key: str, text_fn: Callable[[LauncherConfig], str]) -> None:
        """Copy + VISIBLE confirmation - a silent copy looks like a dead button."""
        button = self._copy_buttons[label_key]
        button.configure(state="disabled")

        def _run() -> None:
            text = text_fn(self._cfg)

            def done() -> None:
                self.clipboard_clear()
                self.clipboard_append(text)
                button.configure(text=self._assistant_labels["copied_to_clipboard"], state="normal")
                self.after(2000, lambda: button.configure(text=self._assistant_labels[label_key]))

            self.after(0, done)

        threading.Thread(target=_run, daemon=True, name=f"dal-gui-{label_key}").start()

    def _build_update_button(self) -> tk.Widget:
        """One-step update (#92): stop -> re-acquire -> start -> health. Routes
        through the shared action machinery (busy state, progress bar, result
        line), so a failed update surfaces its rollback hint like any other
        result. The action self-guards (docker down / not installed)."""
        btn = tk.Button(
            self._secondary_frame,
            text=self._assistant_labels["update_app"],
            command=functools.partial(self._on_action, "update"),
        )
        btn.pack(side="left", padx=4)
        self._update_btn = btn
        return btn

    def _build_log_toggle(self) -> tk.Widget:
        """The log stays collapsed by default but findable (#81): learners see
        headline + checklist + card; the toggle reveals the raw stream. An
        error line auto-expands it - messages are never swallowed."""
        btn = tk.Button(self._secondary_frame, command=self._toggle_log)
        btn.pack(side="left", padx=4)
        self._log_collapsed = False  # set by _set_log_collapsed below
        self._log_toggle_btn = btn
        self._set_log_collapsed(True)
        return btn

    def _toggle_log(self) -> None:
        self._set_log_collapsed(not self._log_collapsed)

    def _set_log_collapsed(self, collapsed: bool) -> None:
        self._log_collapsed = collapsed
        if collapsed:
            self._status_frame.pack_forget()
            self._log_toggle_btn.configure(text=self._assistant_labels["show_details"])
        else:
            self._status_frame.pack(fill="both", expand=True, padx=12, pady=(8, 8), before=self._divider)
            self._log_toggle_btn.configure(text=self._assistant_labels["hide_details"])

    def _t(self, key: str, **kwargs: object) -> str:
        return i18n.t(key, self._cfg, **kwargs)

    def _on_app_logs(self) -> None:
        """App-logs button (#72): one-shot tail normally; while RUNNING it
        toggles a live follow (stream_app_logs) instead - no busy state, the
        window stays fully usable while lines arrive."""
        if self._log_follow_stop is not None:
            self._log_follow_stop.set()
            return
        if getattr(self, "_focused_state", None) == "running":
            self._start_log_follow()
        else:
            self._on_action("app_logs")

    def _start_log_follow(self) -> None:
        stop = threading.Event()
        self._log_follow_stop = stop
        self._buttons["app_logs"].configure(text=self._t("app_logs_follow_stop"))

        def marshal_line(line: str) -> None:
            self.after(0, functools.partial(self._log, line))

        def worker() -> None:
            result = run_guarded(
                "app_logs_follow",
                lambda: actions.stream_app_logs(
                    self._cfg,
                    on_line=marshal_line,
                    should_stop=stop.is_set,
                ),
            )
            self.after(0, functools.partial(self._end_log_follow, result))

        threading.Thread(target=worker, daemon=True, name="dal-gui-log-follow").start()

    def _end_log_follow(self, result: tuple[bool, str] | None) -> None:
        self._log_follow_stop = None
        self._buttons["app_logs"].configure(text=self._t(BUTTON_LABELS["app_logs"]))
        if result is not None and not result[0]:
            self._log(result[1], tag="err")

    def _stop_log_follow_if_running(self) -> None:
        if self._log_follow_stop is not None:
            self._log_follow_stop.set()

    def _poll_focus_request(self) -> None:
        """Bring the window up when a second launch asked for focus (#31)."""
        if lockfile.consume_focus_request(self._cfg.lock_path):
            self._bring_to_front()
        self.after(1000, self._poll_focus_request)

    def _on_window_configure(self, event: tk.Event[tk.Misc]) -> None:
        """Re-wrap the state text to the CURRENT window width (#47).

        The toplevel sits in every child's bindtags, so this fires for child
        configures too - the widget guard keeps it to real window resizes.
        """
        if event.widget is self and event.width > 1:
            self._state_label.configure(wraplength=max(200, event.width - 40))

    def _log(self, line: str, *, tag: str = "info") -> None:
        log_panel_line(line, tag)
        if tag == "err" and getattr(self, "_log_collapsed", False):
            self._set_log_collapsed(False)  # errors must never hide behind the toggle
        self._status.configure(state="normal")
        self._status.insert("end", line + "\n", tag)
        self._status.see("end")
        self._status.configure(state="disabled")

    def report_callback_exception(self, exc_type: type, exc_value: BaseException, exc_tb: object) -> None:
        """Tk swallows callback exceptions (stderr only, invisible from a
        .desktop launch). Log them AND surface them in the panel (P1)."""
        logger.error("uncaught exception in Tk callback", exc_info=(exc_type, exc_value, exc_tb))  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            self._log(self._t("error", msg=str(exc_value)), tag="err")

    def _clear_status(self) -> None:
        self._status.configure(state="normal")
        self._status.delete("1.0", "end")
        self._status.configure(state="disabled")

    def _copy_log(self) -> None:
        """Copy the entire log contents to the clipboard.

        An empty log is a no-op (no clipboard change, no crash). On success the
        button label flips to a localized "Copied!" for ~2s, then restores, so
        the user gets visible feedback that the copy happened.
        """
        content = self._status.get("1.0", "end").strip()
        if not content:
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self._copy_log_btn.configure(text=self._t("log_copied"))
        self.after(2000, lambda: self._copy_log_btn.configure(text=self._t("log_copy")))

    # --- rendering ---

    def _refresh(self) -> None:
        state = actions.get_state(self._cfg)
        if state == "no_docker":
            self._render_docker_help()
        else:
            heading = self._t(_STATE_KEYS.get(state, "no_docker"), port=actions.resolve_port(self._cfg))
            self._apply_status_headline(state)
            self._state_label.configure(text=f"{self._headline_symbol} {heading}", justify="center")
            self._hide_docker_help()
        self._port_entry.configure(state="normal" if port_editable(state) else "disabled")
        self._validate_port()
        self._update_button_states(state)

    def _update_button_states(self, state: str) -> None:
        """Enable/disable every button for ``state`` and refresh its tooltip."""
        for name, btn in self._buttons.items():
            enabled = button_enabled(state, name)
            btn.configure(state="normal" if enabled else "disabled")
            reason = disabled_reason_key(name, state)
            self._tooltips[name].set_text(self._t(reason) if reason else "")
        self._apply_initial_focus(state)
        if state != "running":
            self._stop_log_follow_if_running()

    def _apply_initial_focus(self, state: str) -> None:
        """Keyboard focus lands on the state's primary action (#31) - only on
        a real state CHANGE, so polling refreshes never steal the focus from
        the port field."""
        if state == getattr(self, "_focused_state", None):
            return
        self._focused_state = state
        target = initial_focus_button(state)
        if button_enabled(state, target):
            self._buttons[target].focus_set()

    def _relabel_buttons(self) -> None:
        """Re-apply translated labels to every fixed button (language change)."""
        for name, btn in self._buttons.items():
            btn.configure(text=self._t(BUTTON_LABELS[name]))

    def _validate_port(self) -> None:
        raw = self._port_var.get().strip()
        if not raw.isdigit():
            self._port_indicator.configure(text="✗", fg="#c5221f")
            return
        free, _ = actions.check_port(int(raw))
        self._port_indicator.configure(text="✓" if free else "✗", fg="#188038" if free else "#c5221f")

    # --- docker help (no-docker state) ---

    def _render_docker_help(self) -> None:
        """Platform-specific Docker diagnostics + actions for the no-docker state."""
        for child in self._docker_help_frame.winfo_children():
            child.destroy()
        info = actions.check_docker_detailed(self._cfg, on_step=self._log)
        text = info.get("detail") or self._t("no_docker")
        if info.get("command"):
            text += "\n" + info["command"]
        self._state_label.configure(text=text, justify="center")
        tk.Button(
            self._docker_help_frame,
            text=self._t("retry"),
            width=16,
            command=functools.partial(self._on_action, "recheck"),
        ).pack(side="left", padx=4)
        if info.get("can_start"):
            tk.Button(
                self._docker_help_frame,
                text=self._t("start_docker"),
                width=16,
                command=functools.partial(self._start_docker, info),
            ).pack(side="left", padx=4)
        if info.get("can_fix_permission"):
            tk.Button(
                self._docker_help_frame,
                text=self._t("fix_docker_permission"),
                width=22,
                command=self._fix_docker_permission,
            ).pack(side="left", padx=4)
        if not info.get("installed"):
            tk.Button(
                self._docker_help_frame,
                text=self._t("open_install_guide"),
                width=22,
                command=functools.partial(actions.open_url, info["install_url"]),
            ).pack(side="left", padx=4)
        if not self._docker_help_frame.winfo_ismapped():
            self._docker_help_frame.pack(pady=(0, 4), before=self._primary_frame)

    def _show_about(self) -> None:
        """About dialog: version, platform, backend, endpoint - and the next
        step for a bug report (open the issue tracker) (#30)."""
        text = "\n".join(about_lines(self._cfg)) + "\n\n" + self._t("about_open_issues")
        if messagebox.askyesno(self._t("about"), text, parent=self):
            actions.open_url(issue_tracker_url(self._cfg))

    def _confirm_uninstall(self) -> None:
        """Uninstall is destructive (containers + images) - one accidental
        click must not trigger it."""
        if messagebox.askyesno(self._cfg.app_name, self._t("confirm_uninstall", app=self._cfg.app_name), parent=self):
            self._on_action("uninstall")

    def _fix_docker_permission(self) -> None:
        """Self-repair for the docker-group case (#27): confirm (the docker
        group effectively grants root), then ``pkexec usermod`` off-thread.
        The result message always keeps the re-login requirement visible."""
        if not messagebox.askyesno(self._t("fix_docker_permission"), self._t("docker_group_confirm"), parent=self):
            self._log(self._t("docker_group_cancelled"))
            return
        self._set_busy(True)

        def worker() -> None:
            result = run_guarded("fix_permission", lambda: actions.add_user_to_docker_group(self._cfg))
            self.after(0, lambda: self._on_result("fix_permission", result))

        threading.Thread(target=worker, daemon=True).start()

    def _hide_docker_help(self) -> None:
        if self._docker_help_frame.winfo_ismapped():
            self._docker_help_frame.pack_forget()

    def _start_docker(self, info: dict[str, object]) -> None:
        """Start the Docker daemon (Linux) or Docker Desktop (Win/macOS), then
        WAIT for it: Docker Desktop boots a VM, so an immediate recheck would
        report "not started" again although the start worked (#28)."""
        self._set_busy(True)

        def worker() -> None:
            def body() -> tuple[bool, str]:
                if info.get("platform") == "Linux":
                    result = actions.start_docker_daemon()
                else:
                    result = actions.start_docker_desktop(self._cfg)
                if result[0]:
                    result = actions.wait_for_docker(self._cfg, on_progress=self._on_progress)
                    self.after(0, self._hide_progress)
                return result

            self.after(0, functools.partial(self._on_result, "start_docker", run_guarded("start_docker", body)))

        threading.Thread(target=worker, daemon=True).start()

    # --- advanced (internal ports, experts) ---

    def _build_advanced_section(self) -> None:
        """Build the collapsed expert section for internal (container) ports."""
        self._advanced_open = False
        self._advanced_toggle_row = tk.Frame(self)
        # At first build the primary grid does not exist yet (natural order); on a
        # rebuild (language change) it does, so anchor before it to keep position.
        if hasattr(self, "_primary_frame"):
            self._advanced_toggle_row.pack(pady=(0, 4), before=self._primary_frame)
        else:
            self._advanced_toggle_row.pack(pady=(0, 4))
        self._advanced_toggle = tk.Button(
            self._advanced_toggle_row,
            text="▶ " + self._t("advanced_settings"),
            relief="flat",
            command=self._toggle_advanced,
        )
        self._advanced_toggle.pack()

        self._advanced_frame = tk.Frame(self)
        for name, label, value in internal_port_fields(self._cfg):
            row = tk.Frame(self._advanced_frame)
            row.pack(pady=2)
            tk.Label(row, text=label, width=22, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(value))
            self._internal_vars[name] = var
            tk.Entry(row, textvariable=var, width=8).pack(side="left")
            tk.Button(row, text=self._t("apply"), command=functools.partial(self._apply_internal_port, name)).pack(
                side="left", padx=(6, 0)
            )
        tk.Label(
            self._advanced_frame,
            text="⚠ " + self._t("advanced_warning"),
            wraplength=440,
            justify="left",
            fg="#b06000",
        ).pack(pady=(4, 2))
        tk.Button(self._advanced_frame, text=self._t("restore_defaults"), command=self._restore_internal_defaults).pack(
            pady=(0, 4)
        )

    def _toggle_advanced(self) -> None:
        """Expand/collapse the expert section (collapsed by default)."""
        self._advanced_open = not self._advanced_open
        arrow = "▼ " if self._advanced_open else "▶ "
        self._advanced_toggle.configure(text=arrow + self._t("advanced_settings"))
        if self._advanced_open:
            self._advanced_frame.pack(pady=(0, 6), before=self._primary_frame)
        else:
            self._advanced_frame.pack_forget()

    def _apply_internal_port(self, name: str) -> None:
        """Confirm (rebuild warning) then change one internal port."""
        raw = self._internal_vars[name].get().strip()
        if not raw.isdigit() or not actions._validate_internal_port(int(raw))[0]:
            self._log(self._t("port_invalid", min=actions.MIN_INTERNAL_PORT, max=actions.MAX_PORT), tag="err")
            return
        if not messagebox.askyesno(self._cfg.app_name, self._t("internal_port_confirm")):
            return
        port = int(raw)
        self._set_busy(True)

        def step(label: str) -> None:
            self.after(0, lambda: self._log(label))

        def output(line: str) -> None:
            self.after(0, functools.partial(self._log, line))

        def worker() -> None:
            result = run_guarded(
                "change_internal_port",
                lambda: actions.change_internal_port(self._cfg, name, port, on_step=step, on_output=output),
            )
            self.after(0, lambda: self._on_result("change_internal_port", result))

        threading.Thread(target=worker, daemon=True).start()

    def _restore_internal_defaults(self) -> None:
        """Repopulate the internal-port fields with the config defaults (UI only).

        Persisting + rebuilding still happens through each field's Apply button,
        so this never leaves the running stack half-changed.
        """
        for name, value in default_internal_ports(self._cfg).items():
            if name in self._internal_vars:
                self._internal_vars[name].set(str(value))
        self._log(self._t("restore_defaults"))

    # --- language ---

    def _on_locale_change(self, _event: object = None) -> None:
        """Switch the UI language from the dropdown: persist + re-render in place."""
        code = locale_for_label(self._locale_var.get())
        if code is None or code == self._cfg.locale:
            return
        self._cfg.locale = actions.set_locale(self._cfg, code)
        self._reload_ui_strings()

    def _reload_ui_strings(self) -> None:
        """Re-render every translated label after a language change (no restart)."""
        self._relabel_buttons()
        self._refresh()  # heading + docker-help + button states/tooltips
        if hasattr(self, "_advanced_toggle_row"):
            was_open = getattr(self, "_advanced_open", False)
            self._advanced_toggle_row.destroy()
            self._advanced_frame.destroy()
            self._internal_vars = {}
            self._build_advanced_section()
            if was_open:
                self._toggle_advanced()

    # --- update check ---

    def _check_for_update(self) -> None:
        """Kick off the background update check; log a note when newer exists."""

        def on_update(tag: str, url: str) -> None:
            self.after(0, lambda: self._log(self._t("update_available", tag=tag, url=url)))

        update_check.check_for_update_async(self._cfg, on_update)

    # --- cleanup ---

    def _run_manual_cleanup(self) -> None:
        """Manual 'Cleanup' button: scan for leftover artifacts on demand, then
        either show the selection offer or report that nothing was found.

        Always available whenever Docker is up (not_installed/stopped/running)
        and fully decoupled from the startup offer (which only fires once at
        launch when artifacts already exist). The scan runs off the Tk thread;
        results are marshaled back via ``after``.
        """
        self._log(self._t("cleanup_scanning"))

        def scan() -> None:
            try:
                stale = actions.find_stale_artifacts(self._cfg)
            except Exception as exc:  # noqa: BLE001 - report, never crash the action
                # Bind the message now: ``exc`` is cleared when the except block
                # exits, but the lambda runs later (deferred via ``after``).
                message = str(exc)
                self.after(0, lambda: self._log(self._t("error", msg=message), tag="err"))
                return
            if actions.has_stale_artifacts(stale):
                self.after(0, lambda: self._show_cleanup_offer(stale))
            else:
                self.after(0, lambda: self._log(self._t("cleanup_none")))

        threading.Thread(target=scan, daemon=True).start()

    # --- startup cleanup offer ---

    def _offer_cleanup_if_stale(self) -> None:
        def scan() -> None:
            try:
                stale = actions.find_stale_artifacts(self._cfg)
            except Exception:  # noqa: BLE001 - the offer is non-critical
                return
            if actions.has_stale_artifacts(stale):
                self.after(0, lambda: self._show_cleanup_offer(stale))

        threading.Thread(target=scan, daemon=True).start()

    def _show_cleanup_offer(self, stale: dict[str, list[object]]) -> None:
        self._log(self._t("cleanup_found"))
        for line in actions.cleanup_offer_lines(self._cfg, stale):
            self._log("  " + line)
        offer = tk.Frame(self)
        offer.pack(pady=(0, 8))

        def run_cleanup() -> None:
            offer.destroy()
            self._run_cleanup(stale)

        def skip() -> None:
            offer.destroy()
            self._log(self._t("cleanup_skipped"))

        tk.Button(offer, text=self._t("cleanup_now"), width=18, command=run_cleanup).pack(side="left", padx=4)
        tk.Button(offer, text=self._t("skip"), width=18, command=skip).pack(side="left", padx=4)

    def _run_cleanup(self, stale: dict[str, list[object]]) -> None:
        self._set_busy(True)

        def step(label: str) -> None:
            self.after(0, lambda: self._log(label))

        def worker() -> None:
            result = run_guarded(
                "cleanup", lambda: actions.cleanup_stale(self._cfg, stale, on_step=step, on_progress=self._on_progress)
            )
            self.after(0, lambda: self._on_result("cleanup", result))

        threading.Thread(target=worker, daemon=True).start()

    # --- progress bar ---

    def _on_progress(self, percent: int | None, label: str) -> None:
        """Thread-safe: marshal a progress update onto the Tk thread."""
        self.after(0, lambda: self._update_progress(percent, label))

    def _update_progress(self, percent: int | None, label: str) -> None:
        if not self._progress_frame.winfo_ismapped():
            self._progress_frame.pack(fill="x", before=self._divider)
        self._progress_label.configure(text=label)
        if percent is None:  # indeterminate: unknown duration (e.g. health check)
            self._progress.configure(mode="indeterminate")
            self._progress.start(12)
        else:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._progress["value"] = percent
            if percent >= 100:
                self.after(2000, self._hide_progress)

    def _hide_progress(self) -> None:
        try:
            self._progress.stop()
            self._progress["value"] = 0
            self._progress_frame.pack_forget()
        except tk.TclError as exc:  # pragma: no cover - WM dependent
            logger.debug("could not hide progress bar: %s", exc)

    # --- actions (threaded) ---

    def _on_action(self, action_id: str) -> None:
        raw = self._port_var.get().strip()
        port = int(raw) if raw.isdigit() else None
        # Persist the typed port only for actions that (re)create the stack from
        # scratch; ``change_port`` persists it itself, and the running-state
        # buttons (open/stop/uninstall) must NOT silently move the port out from
        # under the live container (that re-introduces the launcher<->Compose
        # mismatch this fix closes).
        if action_id in ("install", "start") and port is not None:
            actions.set_port(self._cfg, port)
        # A build only happens on install/start/update; arm the cancel signal
        # for those so closing the window mid-build terminates the subprocess
        # (#60). Update rebuilds/re-pulls via start(), so it is a build too.
        builds = action_id in ("install", "start", "update")
        if builds:
            self._cancel_build.clear()
            self._build_in_progress = True
        self._set_busy(True)

        def step(label: str) -> None:
            self.after(0, lambda: self._log(label))

        def output(line: str) -> None:
            self.after(0, functools.partial(self._log, line))

        def worker() -> None:
            result = run_guarded(
                action_id,
                lambda: dispatch_action(
                    action_id,
                    self._cfg,
                    port=port,
                    on_step=step,
                    on_output=output,
                    on_progress=self._on_progress,
                    should_cancel=self._cancel_build.is_set if builds else None,
                ),
            )
            self.after(0, lambda: self._on_result(action_id, result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_result(self, action_id: str, result: tuple[bool, str] | None) -> None:
        self._build_in_progress = False
        self._set_busy(False)
        if result is not None:
            ok, msg = result
            self._log(msg, tag="ok" if ok else "err")
            if not ok and self._cfg.on_error is not None:
                try:
                    self._cfg.on_error(self._cfg, msg)
                except Exception as exc:  # noqa: BLE001 - hook must never break the UI
                    logger.warning("on_error callback failed: %s", exc)
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        """Toggle the window between idle and "an action is running".

        Disables EVERY button in the window - not just the action rows, but any
        transient buttons too (e.g. the cleanup offer) - so a running action
        can never be triggered a second time or have a different action started
        in parallel. While busy the window is forced ``-topmost`` so it cannot
        vanish behind a shell window or dialog that pops up mid-install; when
        the action finishes the flag is dropped (so it does not nag during
        normal use), the window is brought to the front once, and
        :meth:`_refresh` restores each button to its per-state enabled value.
        """
        for btn in self._iter_buttons():
            btn["state"] = "disabled" if busy else "normal"
        self._set_topmost(busy)
        if busy:
            self._clear_status()
            self._log(self._t("installing"))
        else:
            self._bring_to_front()

    def _iter_buttons(self) -> list[tk.Button]:
        """Every ``tk.Button`` currently in the window, walked fresh each call
        so buttons created after start-up (the cleanup offer) are included."""
        found: list[tk.Button] = []
        stack: list[tk.Misc] = list(self.winfo_children())
        while stack:
            widget = stack.pop()
            if isinstance(widget, tk.Button):
                found.append(widget)
            stack.extend(widget.winfo_children())
        return found

    def _set_topmost(self, on: bool) -> None:
        """Best-effort ``-topmost`` toggle; never let a WM quirk break the UI."""
        try:
            self.attributes("-topmost", on)
        except tk.TclError as exc:  # pragma: no cover - platform/WM dependent
            logger.debug("could not set -topmost=%s: %s", on, exc)

    def _bring_to_front(self) -> None:
        """Raise and focus the window (after an action, or a #31 focus request).

        ``deiconify`` first: a minimized/tray-hidden window must come back
        before lift/focus can have any visible effect.
        """
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError as exc:  # pragma: no cover - platform/WM dependent
            logger.debug("could not bring window to front: %s", exc)

    # --- close / system tray ---

    def _on_close(self) -> None:
        self._stop_log_follow_if_running()
        """X button: keep a running app alive (tray/taskbar), else quit.

        Running + opted-in -> background (tray, or taskbar when the tray is
        unavailable, with a hint). Not running, or opted out -> close.

        A build running in the worker thread is signalled to stop first, so the
        ``docker build`` subprocess is terminated rather than orphaned (#60).
        """
        if self._build_in_progress:
            self._cancel_build.set()
        keep_alive = should_keep_alive_on_close(
            actions.get_state(self._cfg),
            minimize_enabled=self._cfg.tray_enabled and self._cfg.tray_minimize_on_close,
        )
        if not keep_alive:
            self._quit()
            return
        self._go_background(via_close=True)

    def _background_controller(self) -> tray.TrayController:
        """Build a tray controller wired to the window's restore/stop/quit."""
        return tray.TrayController(
            config=self._cfg,
            port=actions.resolve_port(self._cfg),
            labels=tray.menu_labels(self._cfg),
            callbacks={
                "open": lambda: self.after(0, self._restore_window),
                "open_browser": lambda: actions.open_browser(self._cfg),
                "stop": lambda: self.after(0, lambda: self._on_action("stop")),
                "quit": lambda: self.after(0, self._quit),
            },
        )

    def _go_background(self, *, via_close: bool = False) -> None:
        """Run in the background: prefer the system tray, fall back to the taskbar.

        Used by both the explicit "Run in background" button and the X button
        (``via_close``). Logs tray diagnostics first (visible under ``--debug``)
        and gives mode-appropriate feedback in the status area.
        """
        tray.log_diagnostics(self._cfg)
        controller = self._background_controller()
        mode = tray.try_minimize_to_background(self, controller)
        if mode == "tray":
            self._tray = controller
            if not via_close:
                self._log(self._t("background_tray"))
        else:
            self._log(self._t("closed_minimized") if via_close else self._t("background_iconified"))

    def _restore_window(self) -> None:
        self._stop_tray()
        self.deiconify()
        self.lift()
        self._refresh()

    def _stop_tray(self) -> None:
        if self._tray is not None:
            self._tray.stop()
            self._tray = None

    def _quit(self) -> None:
        with contextlib.suppress(tk.TclError):
            actions.set_window_geometry(self._cfg, self.winfo_geometry())
        self._stop_tray()
        self.destroy()


def _set_window_icon(root: tk.Tk, icon_path: str) -> None:
    """Set the window/taskbar icon from ``icon_path``. Never raises."""
    if not icon_path:
        return
    path = Path(icon_path).expanduser()
    if not path.is_file():
        return
    try:
        image = tk.PhotoImage(file=str(path))
        root.iconphoto(True, image)
        root._dal_icon = image  # type: ignore[attr-defined]  # keep a reference
    except Exception as exc:  # noqa: BLE001 - icon is best-effort
        logger.debug("could not set window icon from %s: %s", path, exc)


def run(config: LauncherConfig, *, debug: bool = False) -> int:
    """Launch the persistent window. Returns 0 on normal close."""
    app = LauncherApp(config, debug=debug)
    app.mainloop()
    return 0
