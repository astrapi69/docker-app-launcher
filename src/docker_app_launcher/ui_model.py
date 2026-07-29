"""Framework-neutral UI model: the behaviour contract every frontend shares.

This module owns everything a launcher window DECIDES, with zero widget code:
which buttons exist and where they sit, which are enabled per app state (and
why not, for tooltips), how an action id maps onto the :mod:`actions` layer,
and the close/minimize policy. The Tk frontend renders these tables; any
future frontend (Qt, GTK, web, TUI) imports the SAME tables and helpers, so
behaviour can never drift between frontends.

A frontend is any module exposing ``run(config, *, debug=False) -> int`` -
see :mod:`docker_app_launcher.frontends`.
"""

from __future__ import annotations

import logging
import platform
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from docker_app_launcher import actions, i18n
from docker_app_launcher.config import LauncherConfig

if TYPE_CHECKING:
    from docker_app_launcher.diagnostics_report import DoctorReport

logger = logging.getLogger("docker_app_launcher.ui_model")

# Everything shown in a frontend's log panel is mirrored here, so the panel
# content also lands in launcher.log (P0: a message that only ever existed in
# a closed window is a lost message).
_panel_logger = logging.getLogger("docker_app_launcher.ui.panel")

_PANEL_TAG_LEVELS = {"err": logging.ERROR, "ok": logging.INFO, "info": logging.INFO}


def log_panel_line(line: str, tag: str = "info") -> None:
    """Mirror one GUI log-panel line into the logging system.

    Every frontend's ``_log`` calls this before painting the widget, so the
    persistent log file always carries what the user saw on screen.
    """
    _panel_logger.log(_PANEL_TAG_LEVELS.get(tag, logging.INFO), "%s", line)


def run_guarded(action_label: str, fn: Callable[[], tuple[bool, str] | None]) -> tuple[bool, str] | None:
    """Run a worker-thread body; a crash becomes an ordinary failed result.

    ``actions`` functions promise ``(ok, message)`` and never raise — but a
    bug behind that promise used to kill the worker thread silently, leaving
    the window stuck in its busy state (P1). Routing the exception through
    the normal result path clears busy AND puts the error in the panel/log.
    """
    try:
        return fn()
    except Exception as exc:
        logger.exception("action %r crashed", action_label)
        return False, f"{type(exc).__name__}: {exc}"


# state -> i18n key for the heading.
_STATE_KEYS = {
    "no_docker": "no_docker",
    "not_installed": "not_installed",
    "running": "running",
    "stopped": "stopped",
}

# Primary actions, laid out in a fixed two-column grid:
#   [Install]    [Open browser]
#   [Start]      [Stop]
#   [Uninstall]  [Apply port]
#                [Copy log]
PRIMARY_BUTTONS = ["install", "open_browser", "start", "stop", "uninstall", "apply_port", "copy_log"]

# Explicit (row, column) per primary button. The lone Copy-log button sits in
# the RIGHT column (under Apply port) so the grid stays balanced instead of a
# single button dangling on the left.
PRIMARY_GRID = {
    "install": (0, 0),
    "open_browser": (0, 1),
    "start": (1, 0),
    "stop": (1, 1),
    "uninstall": (2, 0),
    "apply_port": (2, 1),
    "copy_log": (3, 1),
}

# Secondary actions, rendered in a single row BELOW the log under a separator.
SECONDARY_BUTTONS = ["cleanup", "app_logs", "background", "info"]

_LAUNCHER_REPO_URL = "https://github.com/astrapi69/docker-app-launcher"


def launcher_version() -> str:
    """The version of the actually installed package - never hardcoded (#30)."""
    try:
        return version("docker-app-launcher")
    except PackageNotFoundError:  # pragma: no cover - source checkout without install
        return "unknown"


def window_title(config: LauncherConfig) -> str:
    """Window title with the running version: ``My App — v0.15.0`` (#30)."""
    return f"{config.app_name} — v{launcher_version()}"


def about_lines(config: LauncherConfig) -> list[str]:
    """Diagnostic facts for the About dialog: what a bug report needs (#30)."""
    app_ver, source = actions.get_app_version(config)
    app_line = f"App: {config.app_name}"
    if app_ver:
        app_line = f"App: {config.app_name} {app_ver} ({source})"
    lines = [
        f"Launcher: docker-app-launcher v{launcher_version()}",
        app_line,
        f"Platform: {platform.system()} ({platform.machine()})",
        f"GUI backend: {config.gui_backend}",
    ]
    override = actions.docker_host_override()
    if override:
        lines.append(f"Docker endpoint (context fallback): {override}")
    lines.append(f"Repository: {config.repo_url or _LAUNCHER_REPO_URL}")
    return lines


def issue_tracker_url(config: LauncherConfig) -> str:
    """Where a bug report should go: the app's repo, else the launcher's."""
    return (config.repo_url or _LAUNCHER_REPO_URL).rstrip("/") + "/issues"


# button name -> i18n label key.
BUTTON_LABELS = {
    "install": "install",
    "start": "start",
    "open_browser": "open_browser",
    "stop": "stop",
    "uninstall": "uninstall",
    "copy_log": "log_copy",
    "cleanup": "cleanup",
    "app_logs": "app_logs",
    "background": "run_in_background",
    "apply_port": "apply_port",
    "info": "about",
}

# The X is the only way to close the window, so there is no cancel/close button.
# Every button is always visible; this table decides enabled vs disabled per
# state. ``no_docker`` disables everything (the docker-help panel takes over);
# ``cleanup`` + ``copy_log`` are enabled whenever Docker is up (stale artifacts
# can linger even before an install, and the log can already carry output);
# ``background`` only while running.
BUTTON_STATES: dict[str, dict[str, bool]] = {
    "no_docker": {
        "install": False,
        "open_browser": False,
        "start": False,
        "stop": False,
        "uninstall": False,
        "apply_port": False,
        "copy_log": False,
        "cleanup": False,
        "app_logs": False,
        "background": False,
        "info": True,
    },
    "not_installed": {
        "install": True,
        "open_browser": False,
        "start": False,
        "stop": False,
        "uninstall": False,
        "apply_port": False,
        "copy_log": True,
        "cleanup": True,
        "app_logs": False,
        "background": False,
        "info": True,
    },
    "stopped": {
        "install": False,
        "open_browser": False,
        "start": True,
        "stop": False,
        "uninstall": True,
        "apply_port": True,
        "copy_log": True,
        "cleanup": True,
        # A crashed/stopped container's logs are exactly what to inspect.
        "app_logs": True,
        "background": False,
        "info": True,
    },
    "running": {
        "install": False,
        "open_browser": True,
        "start": False,
        "stop": True,
        "uninstall": True,
        "apply_port": True,
        "copy_log": True,
        "cleanup": True,
        "app_logs": True,
        "background": True,
        "info": True,
    },
}


# Which button owns keyboard focus after entering a state (#31): the
# state's most likely next action. ``info`` for no_docker - every other
# button is disabled there and the help panel's transient buttons are not
# part of this fixed table.
INITIAL_FOCUS = {
    "no_docker": "info",
    "not_installed": "install",
    "stopped": "start",
    "running": "open_browser",
}


def initial_focus_button(state: str) -> str:
    """The button that should receive keyboard focus for ``state``."""
    return INITIAL_FOCUS.get(state, "info")


def port_editable(state: str) -> bool:
    """Whether the port field is editable.

    Editable in every state except when Docker is down (nothing can act on the
    stack then). A RUNNING stack can have its host port changed in place via the
    "Apply port" button (Stop -> rewrite ``.env`` -> ``up -d``); see
    :func:`actions.change_port`.
    """
    return state != "no_docker"


def button_enabled(state: str, name: str) -> bool:
    """Whether the button ``name`` is enabled in ``state`` (default disabled)."""
    return BUTTON_STATES.get(state, {}).get(name, False)


def disabled_reason_key(name: str, state: str) -> str:
    """The i18n key explaining WHY ``name`` is disabled in ``state`` (tooltip).

    Returns ``""`` when the button is enabled (no tooltip needed). Pure, so the
    tooltip wording is unit-testable without a display.
    """
    if button_enabled(state, name):
        return ""
    if name == "copy_log":
        return "tooltip_no_log"
    if state == "no_docker":
        return "tooltip_needs_docker"
    if name == "install":
        return "tooltip_already_installed"
    if name == "start":
        return "tooltip_already_running" if state == "running" else "tooltip_not_installed"
    if name == "stop":
        return "tooltip_not_running" if state == "stopped" else "tooltip_not_installed"
    if name == "open_browser":
        return "tooltip_not_running" if state == "stopped" else "tooltip_not_installed"
    if name == "background":
        return "tooltip_only_running"
    # uninstall / apply_port are only disabled in not_installed (and no_docker,
    # handled above).
    return "tooltip_not_installed"


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
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[bool, str] | None:
    """Run the action for ``action_id`` through the actions layer.

    Returns ``(ok, message)`` for actions that report a result, or ``None`` for
    fire-and-forget ids (open, recheck). ``port`` is only consumed by
    ``change_port`` (the in-place host-port change); ``on_progress`` by the
    install/start build phases; ``should_cancel`` lets a build be stopped
    mid-flight (#60). Pure (no widget toolkit) so it is unit-testable by
    mocking ``actions``.
    """
    if action_id == "install":
        return actions.ensure_installed(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
    if action_id == "start":
        return actions.start(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
    if action_id == "change_port":
        if port is None:
            return False, i18n.t("port_invalid", config, min=actions.MIN_PORT, max=actions.MAX_PORT)
        return actions.change_port(config, port, on_step=on_step, on_output=on_output)
    if action_id == "stop":
        return actions.stop(config)
    if action_id == "app_logs":
        return actions.app_logs(config)
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


def should_keep_alive_on_close(state: str, *, minimize_enabled: bool) -> bool:
    """Whether the X button should keep the launcher alive instead of quitting.

    True while the app is RUNNING and the app opts in (``minimize_enabled``):
    the window then goes to the tray, or - when the tray is unavailable - is
    minimized to the taskbar (never silently killed). When the app is not
    running, or the app opted out, the X quits the launcher.
    """
    return state == "running" and minimize_enabled


# --- #81: assistant presentation layer -------------------------------------
# The SINGLE source of structure and texts for the installation assistant.
# Renderers (tk/ctk/qt) decide presentation, never content. Every element
# listed here MUST be rendered by every frontend - enforced structurally by
# tests/test_frontend_parity.py against each frontend's
# ASSISTANT_WIDGET_BUILDERS, so a new element cannot exist in one window only.
ASSISTANT_ELEMENTS: tuple[str, ...] = (
    "status_headline",
    "doctor_checklist",
    "problem_card",
    "copy_diagnosis_button",
    "copy_support_bundle_button",
    "log_toggle",
)

# Check ids that can carry status "error" and therefore NEED the two
# explanation texts (check_<id>_meaning / check_<id>_fix) in all 11 catalogs.
# Parity is enforced PER ID by tests/test_i18n.py - a new error-capable id
# without both texts fails the suite, never ships with an empty card.
ERROR_CHECK_IDS: tuple[str, ...] = (
    "docker_running",
    "install_dir",
    "compose_file_exists",
    "dockerfile_exists",
    "image_source_declared",
    "readiness_blocker",
    "port_drift",
    "health_reachable",
)

# Non-color status markers (accessibility: a state must be readable without
# color; the same symbols the text doctor report uses).
_STATUS_SYMBOL = {"ok": "✓", "error": "✗", "warn": "✗", "info": "·"}


def check_meaning(config: LauncherConfig, check_id: str) -> str:
    """The learner-facing 'What does this mean?' text for an error check."""
    return i18n.t(f"check_{check_id}_meaning", config)


def check_fix(config: LauncherConfig, check_id: str) -> str:
    """The learner-facing 'What you can do' text for an error check."""
    return i18n.t(f"check_{check_id}_fix", config)


def assistant_labels(config: LauncherConfig) -> dict[str, str]:
    """Localized labels for the assistant elements - renderers never invent text."""
    return {
        key: i18n.t(key, config)
        for key in (
            "system_check",
            "copy_diagnosis",
            "copy_support_bundle",
            "copied_to_clipboard",
            "problem_found",
            "what_it_means",
            "what_to_do",
            "no_problems_found",
        )
    }


def doctor_checklist_rows(report: DoctorReport) -> list[tuple[str, str]]:
    """(status, line) per check - the line carries its non-color symbol."""
    return [(c.status, f"{_STATUS_SYMBOL[c.status]} {c.message}") for c in report.checks]


def primary_problem(config: LauncherConfig, report: DoctorReport) -> dict[str, str] | None:
    """The problem card for the FIRST error check, or None when all green.

    Learners see problem class + meaning + fix on top; the raw detail stays
    in the check message (rendered in the collapsible log, never first).
    """
    for check in report.checks:
        if check.status == "error":
            known = check.id in ERROR_CHECK_IDS
            return {
                "id": check.id,
                "title": i18n.t("problem_found", config),
                "message": check.message,
                "meaning_label": i18n.t("what_it_means", config),
                "meaning": check_meaning(config, check.id) if known else "",
                "fix_label": i18n.t("what_to_do", config),
                "fix": check_fix(config, check.id) if known else "",
            }
    return None


def status_headline(config: LauncherConfig, state: str, *, health_ok: bool | None = None) -> tuple[str, str]:
    """(severity, text) for the window's status head.

    severity drives the (redundant, never sole) color; the text carries the
    non-color symbol and the localized state label the windows already use.
    """
    state_label = i18n.t(_STATE_KEYS.get(state, "no_docker"), config, port="").strip()
    if health_ok is False:
        return "error", f"{_STATUS_SYMBOL['error']} {state_label}"
    if state == "running":
        return "ok", f"{_STATUS_SYMBOL['ok']} {state_label}"
    return "info", f"{_STATUS_SYMBOL['info']} {state_label}"


def diagnosis_clipboard_text(config: LauncherConfig) -> str:
    """What 'Copy diagnosis' copies: the full doctor text report."""
    from docker_app_launcher.doctor import collect_doctor_report, render_doctor_text

    return render_doctor_text(collect_doctor_report(config))


def support_bundle_clipboard_text(config: LauncherConfig) -> str:
    """What 'Copy support bundle' copies: the human-readable bundle,
    contents stated first - identical to the CLI --support-bundle output."""
    from docker_app_launcher.doctor import collect_support_bundle

    return collect_support_bundle(config).to_text()
