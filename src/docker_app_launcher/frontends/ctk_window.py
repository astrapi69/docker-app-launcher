"""CustomTkinter frontend: the same launcher window with a modern look.

Renders exactly the same behaviour tables as the classic Tk frontend - both
import :mod:`docker_app_launcher.ui_model`, so button layout, per-state
enablement, tooltip reasons, action dispatch and close policy are identical
by construction. Only the widget layer differs.

Requires the ``ctk`` extra (``pip install docker-app-launcher[ctk]``); select
it with ``"gui_backend": "ctk"`` in the launcher JSON.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import platform as _platform
import threading
import tkinter as tk
from typing import Any

from docker_app_launcher import actions, i18n, lockfile, tray, ui_model, update_check
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig, locale_for_label
from docker_app_launcher.frontends.tk_window import ASSISTANT_WIDGET_BUILDERS, _set_window_icon
from docker_app_launcher.frontends.tooltip import Tooltip as _Tooltip
from docker_app_launcher.ui_model import (
    _STATE_KEYS,
    BUTTON_LABELS,
    PRIMARY_BUTTONS,
    PRIMARY_GRID,
    SECONDARY_BUTTONS,
    about_lines,
    advanced_ports_visible,
    button_enabled,
    default_internal_ports,
    disabled_reason_key,
    dispatch_action,
    initial_focus_button,
    internal_port_fields,
    issue_tracker_url,
    log_panel_line,
    port_editable,
    run_guarded,
    should_keep_alive_on_close,
    window_title,
)

logger = logging.getLogger("docker_app_launcher.frontends.ctk_window")

try:
    import customtkinter as ctk

    HAS_CTK = True
except ImportError:  # pragma: no cover - exercised only without the extra
    ctk = None
    HAS_CTK = False

_OK_COLOR = "#188038"
_ERR_COLOR = "#c5221f"

if HAS_CTK:

    class CtkLauncherApp(ctk.CTk):  # type: ignore[misc]
        """The persistent window, rendered with CustomTkinter widgets."""

        def __init__(self, config: LauncherConfig, *, debug: bool = False) -> None:
            super().__init__()
            config.resolve()
            self._cfg = config
            self._cfg.locale = actions.resolve_locale(self._cfg)
            self._debug = debug
            self._tray: tray.TrayController | None = None
            self._buttons: dict[str, Any] = {}
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
            self._state_label = ctk.CTkLabel(
                self, font=ctk.CTkFont(size=16, weight="bold"), wraplength=config.window_width - 40
            )
            self._state_label.pack(pady=(18, 8))
            self.bind("<Configure>", self._on_window_configure)

            port_row = ctk.CTkFrame(self, fg_color="transparent")
            port_row.pack(pady=(0, 8))
            ctk.CTkLabel(port_row, text="Port:").pack(side="left", padx=(0, 6))
            self._port_var = tk.StringVar(value=str(actions.resolve_port(config)))
            self._port_entry = ctk.CTkEntry(port_row, textvariable=self._port_var, width=80)
            self._port_entry.pack(side="left")
            self._port_indicator = ctk.CTkLabel(port_row, text="", width=20)
            self._port_indicator.pack(side="left", padx=(6, 0))
            self._port_entry.bind("<KeyRelease>", lambda _e: self._validate_port())

            lang_row = ctk.CTkFrame(self, fg_color="transparent")
            lang_row.pack(pady=(0, 6))
            ctk.CTkLabel(lang_row, text="🌐").pack(side="left", padx=(0, 6))
            self._locale_var = tk.StringVar(value=LOCALE_LABELS.get(self._cfg.locale, self._cfg.locale))
            self._locale_combo = ctk.CTkComboBox(
                lang_row,
                variable=self._locale_var,
                values=list(LOCALE_LABELS.values()),
                state="readonly",
                width=170,
                command=lambda _choice: self._on_locale_change(),
            )
            self._locale_combo.pack(side="left")

            self._internal_vars: dict[str, tk.StringVar] = {}
            self._advanced_frame: Any | None = None
            if advanced_ports_visible(config):
                self._build_advanced_section()

            handlers = self._build_button_handlers()

            self._docker_help_frame = ctk.CTkFrame(self, fg_color="transparent")

            self._primary_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._primary_frame.pack(pady=(6, 0))
            for name in PRIMARY_BUTTONS:
                row, column = PRIMARY_GRID[name]
                self._make_button(self._primary_frame, name, handlers[name]).grid(
                    row=row, column=column, padx=4, pady=2
                )
            self._copy_log_btn = self._buttons["copy_log"]

            self._progress_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._progress = ctk.CTkProgressBar(self._progress_frame, mode="determinate")
            self._progress.set(0)
            self._progress.pack(fill="x", padx=12, pady=(6, 0))
            self._progress_label = ctk.CTkLabel(self._progress_frame, text="", anchor="w", font=ctk.CTkFont(size=10))
            self._progress_label.pack(fill="x", padx=12)

            self._status = ctk.CTkTextbox(
                self, wrap="word", state="disabled", font=ctk.CTkFont(family="monospace", size=11)
            )
            self._status.pack(fill="both", expand=True, padx=12, pady=(8, 8))

            self._divider = ctk.CTkFrame(self, height=2)
            self._divider.pack(fill="x", padx=12)
            self._secondary_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._secondary_frame.pack(pady=(6, 10))
            for name in SECONDARY_BUTTONS:
                self._make_button(self._secondary_frame, name, handlers[name]).pack(side="left", padx=4)

            # Installation assistant (#81): identical element set as tk/qt,
            # enforced by tests/test_frontend_parity.py.
            self._assistant_labels = ui_model.assistant_labels(config)
            self._assistant: dict[str, Any] = {}
            for element, builder in ASSISTANT_WIDGET_BUILDERS.items():
                self._assistant[element] = getattr(self, builder)()

            self._log(f"{about_lines(config)[0]} · {config.gui_backend} · {_platform.system()}")
            self._refresh()
            if config.cleanup_on_start:
                self._offer_cleanup_if_stale()
            if config.update_check_enabled:
                self._check_for_update()
            if config.single_instance:
                # A refused second launch drops a focus marker (#31).
                self.after(1000, self._poll_focus_request)

        def _on_app_logs(self) -> None:
            """App-logs button (#72): one-shot tail normally; while RUNNING it
            toggles a live follow - no busy state."""
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

        def _bring_to_front(self) -> None:
            self.deiconify()
            self.lift()
            self.focus_force()

        # --- construction helpers ---

        def _build_button_handlers(self) -> dict[str, Any]:
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

        def _make_button(self, parent: Any, name: str, command: Any) -> Any:
            btn = ctk.CTkButton(parent, text=self._t(BUTTON_LABELS[name]), width=170, command=command)
            # Explicit focus ring (#31): CTk paints none by default.
            btn.bind("<FocusIn>", lambda _e, b=btn: b.configure(border_width=2, border_color="#2a5db0"))
            btn.bind("<FocusOut>", lambda _e, b=btn: b.configure(border_width=0))
            self._buttons[name] = btn
            self._tooltips[name] = _Tooltip(btn)
            return btn

        # --- installation assistant (#81) ---

        def _build_status_headline(self) -> Any:
            return self._state_label

        def _apply_status_headline(self, state: str, *, health_ok: bool | None = None) -> None:
            severity, text = ui_model.status_headline(self._cfg, state, health_ok=health_ok)
            colors = {"ok": "#188038", "error": "#c5221f", "info": None}
            color = colors[severity]
            self._state_label.configure(text_color=color if color else ("gray10", "gray90"))
            self._headline_symbol = text.split(" ", 1)[0]

        def _build_doctor_checklist(self) -> Any:
            btn = ctk.CTkButton(
                self._secondary_frame,
                text=self._assistant_labels["system_check"],
                command=self._on_system_check,
                width=120,
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

        def _render_doctor(self, report: Any) -> bool:
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

        def _build_problem_card(self) -> Any:
            frame = ctk.CTkFrame(self, border_width=1)
            wrap = max(200, self._cfg.window_width - 60)
            font_bold = ctk.CTkFont(weight="bold")
            self._problem_title = ctk.CTkLabel(frame, font=font_bold, anchor="w", wraplength=wrap)
            self._problem_title.pack(fill="x", padx=8, pady=(6, 0))
            self._problem_message = ctk.CTkLabel(frame, anchor="w", justify="left", wraplength=wrap)
            self._problem_message.pack(fill="x", padx=8)
            self._problem_meaning_label = ctk.CTkLabel(
                frame, text=self._assistant_labels["what_it_means"], font=font_bold, anchor="w"
            )
            self._problem_meaning_label.pack(fill="x", padx=8)
            self._problem_meaning = ctk.CTkLabel(frame, anchor="w", justify="left", wraplength=wrap)
            self._problem_meaning.pack(fill="x", padx=8)
            self._problem_fix_label = ctk.CTkLabel(
                frame, text=self._assistant_labels["what_to_do"], font=font_bold, anchor="w"
            )
            self._problem_fix_label.pack(fill="x", padx=8)
            self._problem_fix = ctk.CTkLabel(frame, anchor="w", justify="left", wraplength=wrap)
            self._problem_fix.pack(fill="x", padx=8, pady=(0, 6))
            self._problem_frame = frame
            return frame

        def _show_problem_card(self, card: dict[str, str]) -> None:
            self._problem_title.configure(text=f"✗ {card['title']}: {card['id']}")
            self._problem_message.configure(text=card["message"])
            self._problem_meaning.configure(text=card["meaning"])
            self._problem_fix.configure(text=card["fix"])
            self._problem_frame.pack(fill="x", padx=12, pady=(4, 0), before=self._divider)

        def _hide_problem_card(self) -> None:
            self._problem_frame.pack_forget()

        def _build_copy_diagnosis_button(self) -> Any:
            btn = ctk.CTkButton(
                self._secondary_frame,
                text=self._assistant_labels["copy_diagnosis"],
                command=lambda: self._copy_with_feedback("copy_diagnosis", ui_model.diagnosis_clipboard_text),
                width=130,
            )
            btn.pack(side="left", padx=4)
            self._copy_buttons = getattr(self, "_copy_buttons", {})
            self._copy_buttons["copy_diagnosis"] = btn
            return btn

        def _build_copy_support_bundle_button(self) -> Any:
            btn = ctk.CTkButton(
                self._secondary_frame,
                text=self._assistant_labels["copy_support_bundle"],
                command=lambda: self._copy_with_feedback("copy_support_bundle", ui_model.support_bundle_clipboard_text),
                width=150,
            )
            btn.pack(side="left", padx=4)
            self._copy_buttons["copy_support_bundle"] = btn
            return btn

        def _copy_with_feedback(self, label_key: str, text_fn: Any) -> None:
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

        def _build_update_button(self) -> Any:
            """One-step update (#92): stop -> re-acquire -> start -> health,
            through the shared action machinery. The action self-guards."""
            btn = ctk.CTkButton(
                self._secondary_frame,
                text=self._assistant_labels["update_app"],
                command=functools.partial(self._on_action, "update"),
                width=110,
            )
            btn.pack(side="left", padx=4)
            self._update_btn = btn
            return btn

        def _build_log_toggle(self) -> Any:
            btn = ctk.CTkButton(self._secondary_frame, command=self._toggle_log, width=110)
            btn.pack(side="left", padx=4)
            self._log_toggle_btn = btn
            self._log_collapsed = False
            self._set_log_collapsed(True)
            return btn

        def _toggle_log(self) -> None:
            self._set_log_collapsed(not self._log_collapsed)

        def _set_log_collapsed(self, collapsed: bool) -> None:
            self._log_collapsed = collapsed
            if collapsed:
                self._status.pack_forget()
                self._log_toggle_btn.configure(text=self._assistant_labels["show_details"])
            else:
                self._status.pack(fill="both", expand=True, padx=12, pady=(8, 8), before=self._divider)
                self._log_toggle_btn.configure(text=self._assistant_labels["hide_details"])

        def _t(self, key: str, **kwargs: object) -> str:
            return i18n.t(key, self._cfg, **kwargs)

        # --- log ---

        def _on_window_configure(self, event: tk.Event[tk.Misc]) -> None:
            """Re-wrap the state text to the CURRENT window width (#47)."""
            if event.widget is self and event.width > 1:
                self._state_label.configure(wraplength=max(200, event.width - 40))

        def _log(self, line: str, *, tag: str = "info") -> None:
            log_panel_line(line, tag)
            if tag == "err" and getattr(self, "_log_collapsed", False):
                self._set_log_collapsed(False)  # errors must never hide behind the toggle
            self._status.configure(state="normal")
            self._status.insert("end", line + "\n")
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
                self._state_label.configure(text=f"{self._headline_symbol} {heading}")
                self._hide_docker_help()
            self._port_entry.configure(state="normal" if port_editable(state) else "disabled")
            self._validate_port()
            self._update_button_states(state)

        def _update_button_states(self, state: str) -> None:
            for name, btn in self._buttons.items():
                enabled = button_enabled(state, name)
                btn.configure(state="normal" if enabled else "disabled")
                reason = disabled_reason_key(name, state)
                self._tooltips[name].set_text(self._t(reason) if reason else "")
            self._apply_initial_focus(state)
            if state != "running":
                self._stop_log_follow_if_running()

        def _apply_initial_focus(self, state: str) -> None:
            """Keyboard focus lands on the state's primary action (#31) - only
            on a real state CHANGE, never on polling refreshes."""
            if state == getattr(self, "_focused_state", None):
                return
            self._focused_state = state
            target = initial_focus_button(state)
            if button_enabled(state, target):
                self._buttons[target].focus_set()

        def _relabel_buttons(self) -> None:
            for name, btn in self._buttons.items():
                btn.configure(text=self._t(BUTTON_LABELS[name]))

        def _validate_port(self) -> None:
            raw = self._port_var.get().strip()
            if not raw.isdigit():
                self._port_indicator.configure(text="✗", text_color=_ERR_COLOR)
                return
            free, _ = actions.check_port(int(raw))
            self._port_indicator.configure(text="✓" if free else "✗", text_color=_OK_COLOR if free else _ERR_COLOR)

        def _render_docker_help(self) -> None:
            for child in self._docker_help_frame.winfo_children():
                child.destroy()
            info = actions.check_docker_detailed(self._cfg, on_step=self._log)
            text = info.get("detail") or self._t("no_docker")
            if info.get("command"):
                text += "\n" + info["command"]
            self._state_label.configure(text=text)
            ctk.CTkButton(
                self._docker_help_frame,
                text=self._t("retry"),
                width=150,
                command=functools.partial(self._on_action, "recheck"),
            ).pack(side="left", padx=4)
            if info.get("can_start"):
                ctk.CTkButton(
                    self._docker_help_frame,
                    text=self._t("start_docker"),
                    width=150,
                    command=functools.partial(self._start_docker, info),
                ).pack(side="left", padx=4)
            if info.get("can_fix_permission"):
                ctk.CTkButton(
                    self._docker_help_frame,
                    text=self._t("fix_docker_permission"),
                    width=150,
                    command=self._fix_docker_permission,
                ).pack(side="left", padx=4)
            if not info.get("installed"):
                ctk.CTkButton(
                    self._docker_help_frame,
                    text=self._t("open_install_guide"),
                    width=150,
                    command=lambda: actions.open_url(self._cfg.docker_install_url),
                ).pack(side="left", padx=4)
            if not self._docker_help_frame.winfo_ismapped():
                self._docker_help_frame.pack(pady=(0, 6), before=self._primary_frame)

        def _hide_docker_help(self) -> None:
            if self._docker_help_frame.winfo_ismapped():
                self._docker_help_frame.pack_forget()

        def _start_docker(self, info: dict[str, Any]) -> None:
            self._set_busy(True)

            def worker() -> None:
                def body() -> tuple[bool, str]:
                    if info.get("platform") == "Linux":
                        result = actions.start_docker_daemon()
                    else:
                        result = actions.start_docker_desktop(self._cfg)
                    if result[0]:  # started - now wait for the daemon (VM boot, #28)
                        result = actions.wait_for_docker(self._cfg, on_progress=self._on_progress)
                        self.after(0, self._hide_progress)
                    return result

                self.after(0, functools.partial(self._on_result, "start_docker", run_guarded("start_docker", body)))

            threading.Thread(target=worker, daemon=True).start()

        def _show_about(self) -> None:
            from tkinter import messagebox

            text = "\n".join(about_lines(self._cfg)) + "\n\n" + self._t("about_open_issues")
            if messagebox.askyesno(self._t("about"), text, parent=self):
                actions.open_url(issue_tracker_url(self._cfg))

        def _confirm_uninstall(self) -> None:
            from tkinter import messagebox

            if messagebox.askyesno(
                self._cfg.app_name, self._t("confirm_uninstall", app=self._cfg.app_name), parent=self
            ):
                self._on_action("uninstall")

        def _fix_docker_permission(self) -> None:
            """Self-repair for the docker-group case (#27): confirm (docker
            group = effectively root), then pkexec usermod off-thread."""
            from tkinter import messagebox

            if not messagebox.askyesno(self._t("fix_docker_permission"), self._t("docker_group_confirm"), parent=self):
                self._log(self._t("docker_group_cancelled"))
                return
            self._set_busy(True)

            def worker() -> None:
                result = run_guarded("fix_permission", lambda: actions.add_user_to_docker_group(self._cfg))
                self.after(0, lambda: self._on_result("fix_permission", result))

            threading.Thread(target=worker, daemon=True).start()

        # --- language ---

        def _on_locale_change(self) -> None:
            code = locale_for_label(self._locale_var.get())
            if code is None or code == self._cfg.locale:
                return
            self._cfg.locale = actions.set_locale(self._cfg, code)
            self._relabel_buttons()
            self._refresh()

        # --- advanced internal ports ---

        def _build_advanced_section(self) -> None:
            self._advanced_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._advanced_frame.pack(pady=(0, 6))
            for name, label, value in internal_port_fields(self._cfg):
                row = ctk.CTkFrame(self._advanced_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(row, text=label).pack(side="left", padx=(0, 6))
                var = tk.StringVar(value=str(value))
                self._internal_vars[name] = var
                ctk.CTkEntry(row, textvariable=var, width=80).pack(side="left")
                ctk.CTkButton(
                    row,
                    text=self._t("apply_port"),
                    width=110,
                    command=functools.partial(self._apply_internal_port, name),
                ).pack(side="left", padx=4)
            restore = ctk.CTkButton(
                self._advanced_frame,
                text=self._t("restore_defaults"),
                width=150,
                command=self._restore_internal_defaults,
            )
            restore.pack(pady=(4, 0))

        def _apply_internal_port(self, name: str) -> None:
            raw = self._internal_vars[name].get().strip()
            if not raw.isdigit():
                self._log(self._t("port_invalid", min=1, max=65535), tag="err")
                return
            self._set_busy(True)
            port = int(raw)

            def step(label: str) -> None:
                self.after(0, lambda: self._log(label))

            def worker() -> None:
                result = run_guarded(
                    "change_internal_port",
                    lambda: actions.change_internal_port(self._cfg, name, port, on_step=step, on_output=step),
                )
                self.after(0, lambda: self._on_result("change_internal_port", result))

            threading.Thread(target=worker, daemon=True).start()

        def _restore_internal_defaults(self) -> None:
            for name, value in default_internal_ports(self._cfg).items():
                if name in self._internal_vars:
                    self._internal_vars[name].set(str(value))
            self._log(self._t("restore_defaults"))

        # --- update check ---

        def _check_for_update(self) -> None:
            def on_update(tag: str, url: str) -> None:
                self.after(0, lambda: self._log(self._t("update_available", tag=tag, url=url)))

            update_check.check_for_update_async(self._cfg, on_update)

        # --- cleanup ---

        def _run_manual_cleanup(self) -> None:
            self._log(self._t("cleanup_scanning"))

            def scan() -> None:
                try:
                    stale = actions.find_stale_artifacts(self._cfg)
                except Exception as exc:  # noqa: BLE001 - report, never crash
                    message = str(exc)
                    self.after(0, lambda: self._log(self._t("error", msg=message), tag="err"))
                    return
                if actions.has_stale_artifacts(stale):
                    self.after(0, lambda: self._show_cleanup_offer(stale))
                else:
                    self.after(0, lambda: self._log(self._t("cleanup_none")))

            threading.Thread(target=scan, daemon=True).start()

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
            offer = ctk.CTkFrame(self, fg_color="transparent")
            offer.pack(pady=(0, 8))

            def run_cleanup() -> None:
                offer.destroy()
                self._run_cleanup(stale)

            def skip() -> None:
                offer.destroy()
                self._log(self._t("cleanup_skipped"))

            ctk.CTkButton(offer, text=self._t("cleanup_now"), width=170, command=run_cleanup).pack(side="left", padx=4)
            ctk.CTkButton(offer, text=self._t("skip"), width=170, command=skip).pack(side="left", padx=4)

        def _run_cleanup(self, stale: dict[str, list[object]]) -> None:
            self._set_busy(True)

            def step(label: str) -> None:
                self.after(0, lambda: self._log(label))

            def worker() -> None:
                result = run_guarded(
                    "cleanup",
                    lambda: actions.cleanup_stale(self._cfg, stale, on_step=step, on_progress=self._on_progress),
                )
                self.after(0, lambda: self._on_result("cleanup", result))

            threading.Thread(target=worker, daemon=True).start()

        # --- progress ---

        def _on_progress(self, percent: int | None, label: str) -> None:
            self.after(0, lambda: self._update_progress(percent, label))

        def _update_progress(self, percent: int | None, label: str) -> None:
            if not self._progress_frame.winfo_ismapped():
                self._progress_frame.pack(fill="x", before=self._divider)
            self._progress_label.configure(text=label)
            if percent is None:
                self._progress.configure(mode="indeterminate")
                self._progress.start()
            else:
                self._progress.stop()
                self._progress.configure(mode="determinate")
                self._progress.set(percent / 100)
                if percent >= 100:
                    self.after(2000, self._hide_progress)

        def _hide_progress(self) -> None:
            try:
                self._progress.stop()
                self._progress.set(0)
                self._progress_frame.pack_forget()
            except tk.TclError as exc:  # pragma: no cover - WM dependent
                logger.debug("could not hide progress bar: %s", exc)

        # --- actions (threaded) ---

        def _on_action(self, action_id: str) -> None:
            raw = self._port_var.get().strip()
            port = int(raw) if raw.isdigit() else None
            if action_id in ("install", "start") and port is not None:
                actions.set_port(self._cfg, port)
            self._set_busy(True)

            def step(label: str) -> None:
                self.after(0, lambda: self._log(label))

            def output(line: str) -> None:
                self.after(0, functools.partial(self._log, line))

            def worker() -> None:
                result = run_guarded(
                    action_id,
                    lambda: dispatch_action(
                        action_id, self._cfg, port=port, on_step=step, on_output=output, on_progress=self._on_progress
                    ),
                )
                self.after(0, lambda: self._on_result(action_id, result))

            threading.Thread(target=worker, daemon=True).start()

        def _on_result(self, action_id: str, result: tuple[bool, str] | None) -> None:
            # DEFINED end state for EVERY outcome (#97): stop and hide the
            # progress indicator on success, failure and cancel alike.
            self._hide_progress()
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
            for btn in self._iter_buttons():
                btn.configure(state="disabled" if busy else "normal")
            try:
                self.attributes("-topmost", busy)
            except tk.TclError as exc:  # pragma: no cover - WM dependent
                logger.debug("could not set -topmost=%s: %s", busy, exc)
            if busy:
                self._clear_status()
                self._log(self._t("installing"))
            else:
                try:
                    self.lift()
                    self.focus_force()
                except tk.TclError as exc:  # pragma: no cover - WM dependent
                    logger.debug("could not bring window to front: %s", exc)

        def _iter_buttons(self) -> list[Any]:
            found: list[Any] = []
            stack: list[Any] = list(self.winfo_children())
            while stack:
                widget = stack.pop()
                if isinstance(widget, ctk.CTkButton):
                    found.append(widget)
                stack.extend(widget.winfo_children())
            return found

        # --- close / tray ---

        def _on_close(self) -> None:
            self._stop_log_follow_if_running()
            keep_alive = should_keep_alive_on_close(
                actions.get_state(self._cfg),
                minimize_enabled=self._cfg.tray_enabled and self._cfg.tray_minimize_on_close,
            )
            if not keep_alive:
                self._quit()
                return
            self._go_background(via_close=True)

        def _background_controller(self) -> tray.TrayController:
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
            if self._tray is not None:
                self._tray.stop()
                self._tray = None
            self.deiconify()
            self.lift()
            self._refresh()

        def _quit(self) -> None:
            with contextlib.suppress(tk.TclError):
                actions.set_window_geometry(self._cfg, self.winfo_geometry())
            if self._tray is not None:
                self._tray.stop()
                self._tray = None
            self.destroy()


def run(config: LauncherConfig, *, debug: bool = False) -> int:
    """Launch the CustomTkinter window. Returns 0 on normal close."""
    if not HAS_CTK:
        raise RuntimeError("the CustomTkinter frontend requires the 'ctk' extra: pip install docker-app-launcher[ctk]")
    ctk.set_appearance_mode("system")
    app = CtkLauncherApp(config, debug=debug)
    app.mainloop()
    return 0
