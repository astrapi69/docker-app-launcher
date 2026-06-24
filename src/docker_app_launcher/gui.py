"""The persistent launcher window.

ONE window. It opens, shows the current state, and NEVER closes itself - the
only way to close it is the window's X button. There is no dialog chain:
install / start / stop / uninstall / cleanup all happen in-place, streaming
their progress into the scrollable status area.

The Tk layer is intentionally thin. All behaviour lives in :mod:`actions`, and
the pure helpers below (:func:`port_editable`, :func:`buttons_for_state`,
:func:`dispatch_action`, :func:`should_minimize_to_tray`) carry the decisions
so they are unit-testable without a display.
"""

from __future__ import annotations

import functools
import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from docker_app_launcher import actions, i18n, tray, update_check
from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.gui")

# state -> i18n key for the heading.
_STATE_KEYS = {
    "no_docker": "no_docker",
    "not_installed": "not_installed",
    "running": "running",
    "stopped": "stopped",
}

# state -> [(action_id, i18n_label_key), ...]. The X is the only way to close
# the window, so there is no cancel/close button anywhere.
_BUTTONS: dict[str, list[tuple[str, str]]] = {
    "no_docker": [("recheck", "retry")],
    "not_installed": [("install", "install")],
    "stopped": [("start", "start"), ("uninstall", "uninstall")],
    "running": [("open", "open_browser"), ("change_port", "apply_port"), ("stop", "stop"), ("uninstall", "uninstall")],
}


def port_editable(state: str) -> bool:
    """Whether the port field is editable.

    Editable in every state except when Docker is down (nothing can act on the
    stack then). A RUNNING stack can have its host port changed in place via the
    "Apply port" button (Stop -> rewrite ``.env`` -> ``up -d``); see
    :func:`actions.change_port`.
    """
    return state != "no_docker"


def buttons_for_state(state: str) -> list[tuple[str, str]]:
    """Return ``[(action_id, i18n_label_key), ...]`` for ``state``."""
    return list(_BUTTONS.get(state, []))


def advanced_ports_visible(config: LauncherConfig) -> bool:
    """Whether the expert internal-port section is shown.

    Only when the app opts in (``show_advanced_ports``) AND actually declares
    internal ports to expose (``env_internal_port_keys``); otherwise the section
    is inert and stays hidden.
    """
    return bool(config.show_advanced_ports and config.env_internal_port_keys)


def internal_port_fields(config: LauncherConfig) -> list[tuple[str, str, int]]:
    """Return ``[(name, label, current_value), ...]`` for the expert section.

    One row per declared internal port, label localized via ``internal_port_field``
    and value resolved (stored override or config default). Sorted by name for a
    stable layout.
    """
    rows: list[tuple[str, str, int]] = []
    for name in sorted(config.env_internal_port_keys):
        label = i18n.t("internal_port_field", config, name=name.capitalize())
        rows.append((name, label, actions.resolve_internal_port(config, name)))
    return rows


def default_internal_ports(config: LauncherConfig) -> dict[str, int]:
    """The config-default internal ports (what "Restore defaults" repopulates)."""
    return dict(config.internal_ports)


def dispatch_action(
    action_id: str,
    config: LauncherConfig,
    *,
    port: int | None = None,
    on_step: actions.ProgressFn | None = None,
    on_output: actions.OutputFn | None = None,
) -> tuple[bool, str] | None:
    """Run the action for ``action_id`` through the actions layer.

    Returns ``(ok, message)`` for actions that report a result, or ``None`` for
    fire-and-forget ids (open, recheck). ``port`` is only consumed by
    ``change_port`` (the in-place host-port change). Pure (no Tk) so it is
    unit-testable by mocking ``actions``.
    """
    if action_id == "install":
        return actions.ensure_installed(config, on_step=on_step, on_output=on_output)
    if action_id == "start":
        return actions.start(config, on_step=on_step, on_output=on_output)
    if action_id == "change_port":
        if port is None:
            return False, i18n.t("port_invalid", config, min=actions.MIN_PORT, max=actions.MAX_PORT)
        return actions.change_port(config, port, on_step=on_step, on_output=on_output)
    if action_id == "stop":
        return actions.stop(config)
    if action_id == "uninstall":
        return actions.uninstall(config, on_step=on_step)
    if action_id == "open":
        actions.open_browser(config)
        return None
    if action_id == "recheck":
        return None
    logger.warning("unknown action_id: %s", action_id)
    return None


def should_minimize_to_tray(state: str, *, tray_available: bool, tray_enabled: bool) -> bool:
    """Whether closing the window should minimize to the tray.

    Minimize only when the app is RUNNING, the tray is enabled in config, and
    the tray extra is available; otherwise the X closes the launcher.
    """
    return state == "running" and tray_enabled and tray_available


def background_button_visible(state: str) -> bool:
    """Whether the explicit "Run in background" button is shown (running only)."""
    return state == "running"


def should_keep_alive_on_close(state: str, *, minimize_enabled: bool) -> bool:
    """Whether the X button should keep the launcher alive instead of quitting.

    True while the app is RUNNING and the app opts in (``minimize_enabled``):
    the window then goes to the tray, or - when the tray is unavailable - is
    minimized to the taskbar (never silently killed). When the app is not
    running, or the app opted out, the X quits the launcher.
    """
    return state == "running" and minimize_enabled


class LauncherApp(tk.Tk):
    """The persistent window. Thin Tk over the helpers above."""

    def __init__(self, config: LauncherConfig, *, debug: bool = False) -> None:
        super().__init__()
        config.resolve()
        self._cfg = config
        self._debug = debug
        self._tray: tray.TrayController | None = None

        self.title(config.app_name)
        self.geometry(f"{config.window_width}x{config.window_height}")
        if not config.window_resizable:
            self.resizable(False, False)
        self.minsize(min(600, config.window_width), min(420, config.window_height))
        _set_window_icon(self, config.icon_path)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._state_label = tk.Label(self, font=("Segoe UI", 12, "bold"))
        self._state_label.pack(pady=(18, 8))

        port_row = tk.Frame(self)
        port_row.pack(pady=(0, 8))
        tk.Label(port_row, text="Port:").pack(side="left", padx=(0, 6))
        self._port_var = tk.StringVar(value=str(actions.resolve_port(config)))
        self._port_entry = tk.Entry(port_row, textvariable=self._port_var, width=8)
        self._port_entry.pack(side="left")
        self._port_indicator = tk.Label(port_row, text="", width=2)
        self._port_indicator.pack(side="left", padx=(6, 0))
        self._port_entry.bind("<KeyRelease>", lambda _e: self._validate_port())

        self._internal_vars: dict[str, tk.StringVar] = {}
        if advanced_ports_visible(config):
            self._build_advanced_section()

        self._button_row = tk.Frame(self)
        self._button_row.pack(pady=(4, 0))

        # A separate row below the action buttons for the explicit
        # "Run in background" button (shown only while the app is running).
        self._background_row = tk.Frame(self)
        self._background_row.pack(pady=(2, 0))

        status_frame = tk.Frame(self)
        status_frame.pack(fill="both", expand=True, padx=12, pady=(8, 12))
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

        self._refresh()
        if config.cleanup_on_start:
            self._offer_cleanup_if_stale()
        if config.update_check_enabled:
            self._check_for_update()

    # --- helpers ---

    def _t(self, key: str, **kwargs: object) -> str:
        return i18n.t(key, self._cfg, **kwargs)

    def _log(self, line: str, *, tag: str = "info") -> None:
        self._status.configure(state="normal")
        self._status.insert("end", line + "\n", tag)
        self._status.see("end")
        self._status.configure(state="disabled")

    def _clear_status(self) -> None:
        self._status.configure(state="normal")
        self._status.delete("1.0", "end")
        self._status.configure(state="disabled")

    # --- rendering ---

    def _refresh(self) -> None:
        state = actions.get_state(self._cfg)
        heading = self._t(_STATE_KEYS.get(state, "no_docker"), port=actions.resolve_port(self._cfg))
        self._state_label.configure(text=heading)
        editable = port_editable(state)
        self._port_entry.configure(state="normal" if editable else "disabled")
        self._validate_port()
        for child in self._button_row.winfo_children():
            child.destroy()
        for action_id, label_key in buttons_for_state(state):
            tk.Button(
                self._button_row,
                text=self._t(label_key),
                width=18,
                command=functools.partial(self._on_action, action_id),
            ).pack(side="left", padx=4)
        for child in self._background_row.winfo_children():
            child.destroy()
        if background_button_visible(state):
            tk.Button(
                self._background_row,
                text=self._t("run_in_background"),
                width=38,
                command=self._go_background,
            ).pack()

    def _validate_port(self) -> None:
        raw = self._port_var.get().strip()
        if not raw.isdigit():
            self._port_indicator.configure(text="✗", fg="#c5221f")
            return
        free, _ = actions.check_port(int(raw))
        self._port_indicator.configure(text="✓" if free else "✗", fg="#188038" if free else "#c5221f")

    # --- advanced (internal ports, experts) ---

    def _build_advanced_section(self) -> None:
        """Build the collapsed expert section for internal (container) ports."""
        self._advanced_open = False
        toggle_row = tk.Frame(self)
        toggle_row.pack(pady=(0, 4))
        self._advanced_toggle = tk.Button(
            toggle_row, text="▶ " + self._t("advanced_settings"), relief="flat", command=self._toggle_advanced
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
            self._advanced_frame.pack(pady=(0, 6), before=self._button_row)
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
            result = actions.change_internal_port(self._cfg, name, port, on_step=step, on_output=output)
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

    # --- update check ---

    def _check_for_update(self) -> None:
        """Kick off the background update check; log a note when newer exists."""

        def on_update(tag: str, url: str) -> None:
            self.after(0, lambda: self._log(self._t("update_available", tag=tag, url=url)))

        update_check.check_for_update_async(self._cfg, on_update)

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

        tk.Button(offer, text=self._t("cleanup"), width=18, command=run_cleanup).pack(side="left", padx=4)
        tk.Button(offer, text=self._t("skip"), width=18, command=skip).pack(side="left", padx=4)

    def _run_cleanup(self, stale: dict[str, list[object]]) -> None:
        self._set_busy(True)

        def step(label: str) -> None:
            self.after(0, lambda: self._log(label))

        def worker() -> None:
            result = actions.cleanup_stale(self._cfg, stale, on_step=step)
            self.after(0, lambda: self._on_result("cleanup", result))

        threading.Thread(target=worker, daemon=True).start()

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
        self._set_busy(True)

        def step(label: str) -> None:
            self.after(0, lambda: self._log(label))

        def output(line: str) -> None:
            self.after(0, functools.partial(self._log, line))

        def worker() -> None:
            result = dispatch_action(action_id, self._cfg, port=port, on_step=step, on_output=output)
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
        """Toggle the window between idle and "an action is running".

        Disables EVERY button in the window - not just the action row, but any
        transient buttons too (e.g. the cleanup offer) - so a running action
        can never be triggered a second time or have a different action started
        in parallel. While busy the window is forced ``-topmost`` so it cannot
        vanish behind a shell window or dialog that pops up mid-install; when
        the action finishes the flag is dropped (so it does not nag during
        normal use) and the window is brought to the front once.
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
        """Raise and focus the window once (after an action completes)."""
        try:
            self.lift()
            self.focus_force()
        except tk.TclError as exc:  # pragma: no cover - platform/WM dependent
            logger.debug("could not bring window to front: %s", exc)

    # --- close / system tray ---

    def _on_close(self) -> None:
        """X button: keep a running app alive (tray/taskbar), else quit.

        Running + opted-in -> background (tray, or taskbar when the tray is
        unavailable, with a hint). Not running, or opted out -> close.
        """
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
