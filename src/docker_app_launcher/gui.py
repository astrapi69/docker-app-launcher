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
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from docker_app_launcher import actions, i18n, tray, update_check
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig, locale_for_label

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
    "running": [("open", "open_browser"), ("stop", "stop"), ("uninstall", "uninstall")],
}

# Secondary actions rendered on a SECOND row so the primary row never overflows
# the fixed-width window (the running state would otherwise pack 5 buttons into
# one row and clip "Uninstall"). ``background`` is wired to the run-in-background
# handler and ``cleanup`` to the on-demand cleanup scan; the rest route through
# the normal action dispatch. ``cleanup`` is ALWAYS present in the installed
# states (running/stopped) - it is independent of the startup cleanup offer,
# which only fires once at launch when leftover artifacts already exist.
_SECONDARY_BUTTONS: dict[str, list[tuple[str, str]]] = {
    "running": [("change_port", "apply_port"), ("background", "run_in_background"), ("cleanup", "cleanup")],
    "stopped": [("cleanup", "cleanup")],
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
    """Return the PRIMARY-row ``[(action_id, i18n_label_key), ...]`` for ``state``."""
    return list(_BUTTONS.get(state, []))


def secondary_buttons_for_state(state: str) -> list[tuple[str, str]]:
    """Return the SECOND-row ``[(action_id, i18n_label_key), ...]`` for ``state``.

    Keeps the primary row short enough to fit the fixed-width window.
    """
    return list(_SECONDARY_BUTTONS.get(state, []))


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
    on_progress: actions.ProgressPctFn | None = None,
) -> tuple[bool, str] | None:
    """Run the action for ``action_id`` through the actions layer.

    Returns ``(ok, message)`` for actions that report a result, or ``None`` for
    fire-and-forget ids (open, recheck). ``port`` is only consumed by
    ``change_port`` (the in-place host-port change); ``on_progress`` by the
    install/start build phases. Pure (no Tk) so it is unit-testable by mocking
    ``actions``.
    """
    if action_id == "install":
        return actions.ensure_installed(config, on_step=on_step, on_output=on_output, on_progress=on_progress)
    if action_id == "start":
        return actions.start(config, on_step=on_step, on_output=on_output, on_progress=on_progress)
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
        # Effective UI language: the user's persisted picker choice wins over the
        # config default (which already resolved "auto" -> system locale).
        self._cfg.locale = actions.resolve_locale(self._cfg)
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

        self._button_row = tk.Frame(self)
        self._button_row.pack(pady=(4, 0))

        # A separate row below the action buttons for the explicit
        # "Run in background" button (shown only while the app is running).
        self._background_row = tk.Frame(self)
        self._background_row.pack(pady=(2, 0))

        # Progress bar + label above the log: a quick visual for long actions
        # (install/start build, cleanup), with the scrollable log below for
        # detail. Hidden until an action reports progress.
        self._progress_frame = tk.Frame(self)
        self._progress = ttk.Progressbar(self._progress_frame, mode="determinate", maximum=100)
        self._progress.pack(fill="x", padx=12, pady=(6, 0))
        self._progress_label = tk.Label(self._progress_frame, text="", anchor="w", font=("Segoe UI", 8))
        self._progress_label.pack(fill="x", padx=12)

        # Copy-log toolbar: a small button above the scrollable log so the user
        # can grab the whole log in one click for a bug report / email / chat.
        log_toolbar = tk.Frame(self)
        log_toolbar.pack(fill="x", padx=12, pady=(8, 0))
        self._copy_log_btn = tk.Button(
            log_toolbar,
            text=self._t("log_copy"),
            width=14,
            command=self._copy_log,
        )
        self._copy_log_btn.pack(side="right")

        status_frame = tk.Frame(self)
        self._status_frame = status_frame
        status_frame.pack(fill="both", expand=True, padx=12, pady=(4, 12))
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
        heading = self._t(_STATE_KEYS.get(state, "no_docker"), port=actions.resolve_port(self._cfg))
        self._state_label.configure(text=heading)
        editable = port_editable(state)
        self._port_entry.configure(state="normal" if editable else "disabled")
        self._validate_port()
        for child in self._button_row.winfo_children():
            child.destroy()
        if state == "no_docker":
            self._render_docker_help()
        else:
            for action_id, label_key in buttons_for_state(state):
                tk.Button(
                    self._button_row,
                    text=self._t(label_key),
                    width=18,
                    command=functools.partial(self._on_action, action_id),
                ).pack(side="left", padx=4)
        for child in self._background_row.winfo_children():
            child.destroy()
        for action_id, label_key in secondary_buttons_for_state(state):
            command = self._secondary_command(action_id)
            tk.Button(self._background_row, text=self._t(label_key), width=22, command=command).pack(
                side="left", padx=4
            )

    def _secondary_command(self, action_id: str) -> Callable[[], None]:
        """Resolve the click handler for a second-row button.

        ``background`` and ``cleanup`` have bespoke handlers (the latter scans
        on demand rather than dispatching a one-shot action); everything else
        routes through the normal action dispatch.
        """
        if action_id == "background":
            return self._go_background
        if action_id == "cleanup":
            return self._run_manual_cleanup
        return functools.partial(self._on_action, action_id)

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
        info = actions.check_docker_detailed(self._cfg)
        text = info.get("detail") or self._t("no_docker")
        if info.get("command"):
            text += "\n" + info["command"]
        self._state_label.configure(text=text, justify="center")
        tk.Button(
            self._button_row, text=self._t("retry"), width=16, command=functools.partial(self._on_action, "recheck")
        ).pack(side="left", padx=4)
        if info.get("can_start"):
            tk.Button(
                self._button_row,
                text=self._t("start_docker"),
                width=16,
                command=functools.partial(self._start_docker, info),
            ).pack(side="left", padx=4)
        if not info.get("installed"):
            tk.Button(
                self._button_row,
                text=self._t("open_install_guide"),
                width=22,
                command=functools.partial(actions.open_url, info["install_url"]),
            ).pack(side="left", padx=4)

    def _start_docker(self, info: dict[str, object]) -> None:
        """Start the Docker daemon (Linux) or Docker Desktop (Win/macOS), then recheck."""
        self._set_busy(True)

        def worker() -> None:
            if info.get("platform") == "Linux":
                result = actions.start_docker_daemon()
            else:
                result = actions.start_docker_desktop(self._cfg)
            self.after(0, lambda: self._on_result("start_docker", result))

        threading.Thread(target=worker, daemon=True).start()

    # --- advanced (internal ports, experts) ---

    def _build_advanced_section(self) -> None:
        """Build the collapsed expert section for internal (container) ports."""
        self._advanced_open = False
        self._advanced_toggle_row = tk.Frame(self)
        # At first build the button row does not exist yet (natural order); on a
        # rebuild (language change) it does, so anchor before it to keep position.
        if hasattr(self, "_button_row"):
            self._advanced_toggle_row.pack(pady=(0, 4), before=self._button_row)
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
        self._refresh()  # state heading + primary/secondary button rows
        if hasattr(self, "_copy_log_btn"):
            self._copy_log_btn.configure(text=self._t("log_copy"))
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

        Always available in the installed states (running/stopped) and fully
        decoupled from the startup offer (which only fires once at launch when
        artifacts already exist). The scan runs off the Tk thread; results are
        marshaled back via ``after``.
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

        tk.Button(offer, text=self._t("cleanup"), width=18, command=run_cleanup).pack(side="left", padx=4)
        tk.Button(offer, text=self._t("skip"), width=18, command=skip).pack(side="left", padx=4)

    def _run_cleanup(self, stale: dict[str, list[object]]) -> None:
        self._set_busy(True)

        def step(label: str) -> None:
            self.after(0, lambda: self._log(label))

        def worker() -> None:
            result = actions.cleanup_stale(self._cfg, stale, on_step=step, on_progress=self._on_progress)
            self.after(0, lambda: self._on_result("cleanup", result))

        threading.Thread(target=worker, daemon=True).start()

    # --- progress bar ---

    def _on_progress(self, percent: int | None, label: str) -> None:
        """Thread-safe: marshal a progress update onto the Tk thread."""
        self.after(0, lambda: self._update_progress(percent, label))

    def _update_progress(self, percent: int | None, label: str) -> None:
        if not self._progress_frame.winfo_ismapped():
            self._progress_frame.pack(fill="x", before=self._status_frame)
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
