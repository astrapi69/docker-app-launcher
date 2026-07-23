"""CustomTkinter frontend: the same launcher window with a modern look.

Renders exactly the same behaviour tables as the classic Tk frontend - both
import :mod:`docker_app_launcher.ui_model`, so button layout, per-state
enablement, tooltip reasons, action dispatch and close policy are identical
by construction. Only the widget layer differs.

Requires the ``ctk`` extra (``pip install docker-app-launcher[ctk]``); select
it with ``"gui_backend": "ctk"`` in the launcher JSON.
"""

from __future__ import annotations

import functools
import logging
import threading
import tkinter as tk
from typing import Any

from docker_app_launcher import actions, i18n, tray, update_check
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig, locale_for_label
from docker_app_launcher.gui import _set_window_icon, _Tooltip
from docker_app_launcher.ui_model import (
    _STATE_KEYS,
    BUTTON_LABELS,
    PRIMARY_BUTTONS,
    PRIMARY_GRID,
    SECONDARY_BUTTONS,
    advanced_ports_visible,
    button_enabled,
    default_internal_ports,
    disabled_reason_key,
    dispatch_action,
    internal_port_fields,
    port_editable,
    should_keep_alive_on_close,
)

logger = logging.getLogger("docker_app_launcher.frontends.ctk")

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
            self._tooltips: dict[str, _Tooltip] = {}

            self.title(config.app_name)
            self.geometry(f"{config.window_width}x{config.window_height}")
            if not config.window_resizable:
                self.resizable(False, False)
            self.minsize(min(600, config.window_width), min(420, config.window_height))
            _set_window_icon(self, config.icon_path)
            self.protocol("WM_DELETE_WINDOW", self._on_close)

            self._state_label = ctk.CTkLabel(self, font=ctk.CTkFont(size=16, weight="bold"))
            self._state_label.pack(pady=(18, 8))

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

            self._refresh()
            if config.cleanup_on_start:
                self._offer_cleanup_if_stale()
            if config.update_check_enabled:
                self._check_for_update()

        # --- construction helpers ---

        def _build_button_handlers(self) -> dict[str, Any]:
            return {
                "install": functools.partial(self._on_action, "install"),
                "start": functools.partial(self._on_action, "start"),
                "open_browser": functools.partial(self._on_action, "open"),
                "stop": functools.partial(self._on_action, "stop"),
                "uninstall": functools.partial(self._on_action, "uninstall"),
                "copy_log": self._copy_log,
                "cleanup": self._run_manual_cleanup,
                "background": self._go_background,
                "apply_port": functools.partial(self._on_action, "change_port"),
            }

        def _make_button(self, parent: Any, name: str, command: Any) -> Any:
            btn = ctk.CTkButton(parent, text=self._t(BUTTON_LABELS[name]), width=170, command=command)
            self._buttons[name] = btn
            self._tooltips[name] = _Tooltip(btn)
            return btn

        def _t(self, key: str, **kwargs: object) -> str:
            return i18n.t(key, self._cfg, **kwargs)

        # --- log ---

        def _log(self, line: str, *, tag: str = "info") -> None:
            self._status.configure(state="normal")
            self._status.insert("end", line + "\n")
            self._status.see("end")
            self._status.configure(state="disabled")

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
                self._state_label.configure(text=heading)
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
            info = actions.check_docker_detailed(self._cfg)
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
                if info.get("platform") == "Linux":
                    result = actions.start_docker_daemon()
                else:
                    result = actions.start_docker_desktop(self._cfg)
                self.after(0, lambda: self._on_result("start_docker", result))

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
                result = actions.change_internal_port(self._cfg, name, port, on_step=step, on_output=step)
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

            ctk.CTkButton(offer, text=self._t("cleanup"), width=170, command=run_cleanup).pack(side="left", padx=4)
            ctk.CTkButton(offer, text=self._t("skip"), width=170, command=skip).pack(side="left", padx=4)

        def _run_cleanup(self, stale: dict[str, list[object]]) -> None:
            self._set_busy(True)

            def step(label: str) -> None:
                self.after(0, lambda: self._log(label))

            def worker() -> None:
                result = actions.cleanup_stale(self._cfg, stale, on_step=step, on_progress=self._on_progress)
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
                result = dispatch_action(
                    action_id, self._cfg, port=port, on_step=step, on_output=output, on_progress=self._on_progress
                )
                self.after(0, lambda: self._on_result(action_id, result))

            threading.Thread(target=worker, daemon=True).start()

        def _on_result(self, action_id: str, result: tuple[bool, str] | None) -> None:
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
