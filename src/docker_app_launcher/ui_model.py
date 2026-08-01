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

from docker_app_launcher import actions, check_ids, i18n
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

# The X is the only close control in the window; there is no separate close
# button. Which is exactly why the close policy must always leave an exit -
# see should_keep_alive_on_close and EXIT_PATHS (#108).
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
    if action_id == "update":
        return actions.update(
            config, on_step=on_step, on_output=on_output, on_progress=on_progress, should_cancel=should_cancel
        )
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


def should_keep_alive_on_close(state: str, *, minimize_enabled: bool, tray_available: bool = False) -> bool:
    """Whether the X button should keep the launcher alive instead of quitting.

    True only while the app is RUNNING, the app opts in (``minimize_enabled``)
    AND a tray can actually dock (``tray_available``) - because the tray menu
    is the only place that carries Quit. Without a tray the X CLOSES the
    launcher (#108): the device finding was a window that minimized to the
    taskbar forever, came back on click, and could only be ended through the
    task manager. Closing is safe - the app runs in Docker and keeps running;
    the launcher is a control window, not the app's host process.

    ``tray_available`` defaults to False so a caller that does not know about
    trays gets the exit rather than the trap.
    """
    return state == "running" and minimize_enabled and tray_available


# --- #108: every way OUT of the application ---------------------------------
# Fourth exit-less state found on a device (after the progress bar #97, the
# cancelling state #98 and the pending block #100), and the worst: the
# application itself. So the ways out are ENUMERATED, together with the
# conditions they must work under - a new exit path or a new condition has to
# appear here or tests/test_close_always_has_an_exit.py fails.
EXIT_CONDITIONS: tuple[str, ...] = ("tray_available", "no_tray")

# path id -> the conditions under which this path ENDS the process. The
# invariant the suite proves: EVERY condition is covered by at least one path.
EXIT_PATHS: dict[str, tuple[str, ...]] = {
    # The X: quits without a tray; with a tray it backgrounds instead, and
    # the tray menu then carries the exit.
    "window_close": ("no_tray",),
    # The tray menu's Quit entry - only exists while an icon is docked.
    "tray_menu_quit": ("tray_available",),
}


def action_display_name(config: LauncherConfig, action_id: str) -> str:
    """Localized name of an action for user messages (falls back to the id)."""
    key = BUTTON_LABELS.get(action_id, action_id)
    text = i18n.t(key, config)
    return action_id if text == key else text


def exit_paths_for(condition: str) -> tuple[str, ...]:
    """Which exit paths end the process under ``condition``. Never empty."""
    if condition not in EXIT_CONDITIONS:
        raise ValueError(f"unknown exit condition: {condition!r}")
    return tuple(path for path, conditions in EXIT_PATHS.items() if condition in conditions)


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
    # One-step update (#92): stop -> re-acquire -> start -> health. Listed here
    # so every frontend MUST render it (tests/test_frontend_parity.py) - the
    # same drift-proofing the rest of the assistant gets.
    "update_button",
    # Cancel (#98): visible only while a CANCELLABLE action runs; stays
    # enabled while everything else is busy-disabled; a second click is
    # ignored (the label flips to "cancelling"), and the watchdog guarantees
    # an exit if the operation never answers.
    "cancel_button",
)

# Check ids that can carry status "error" and therefore NEED the two
# explanation texts (check_<id>_meaning / check_<id>_fix) in all 11 catalogs.
# Parity is enforced PER ID by tests/test_i18n.py - a new error-capable id
# without both texts fails the suite, never ships with an empty card.
# The concurrency guard's user-visible notes (#106): every key listed here
# must have a mention in the user docs. tests/test_user_docs_coverage.py
# enforces that AND pins this tuple to the i18n keys actually used in
# check_pending_operation - a new guard note cannot ship undocumented.
GUARD_USER_NOTE_KEYS: tuple[str, ...] = (
    "guard_unavailable",
    "pending_expired_unconfirmed",
    "operation_pending_blocked",
)

# DERIVED from what the ids actually emit (#127), not listed. The old literal
# got the membership right and the REASON wrong - it called bind_address_open
# error-capable when it is the only warn emitter in the project. Kept under the
# old name: consumers import it.
ERROR_CHECK_IDS: tuple[str, ...] = check_ids.NEEDS_EXPLANATION_IDS

# Non-color status markers (accessibility: a state must be readable without
# color; the same symbols the text doctor report uses).
# warn has its OWN symbol (#127). It used to share ✗ with error - the same
# conflation as the card, one layer down: a running app that is merely
# reachable from the network is not a broken one.
_STATUS_SYMBOL = {"ok": "✓", "error": "✗", "warn": "!", "info": "·"}


#: Which severity the card shows first. Error before warning: something broken
#: outranks something merely open. Both are explained; neither is called the
#: other.
_SEVERITY_ORDER: tuple[str, ...] = ("error", "warn")

#: The heading per severity - the whole point of the concept fix. A warning
#: under "problem found" is a small dishonesty that would then apply to every
#: warning the launcher ever grows.
_SEVERITY_TITLE_KEYS = {"error": "problem_found", "warn": "warning_found"}


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
            "show_details",
            "hide_details",
            "update_app",
            "cancel_operation",
            "cancelling",
            "cancel_unresponsive",
        )
    }


def doctor_checklist_rows(report: DoctorReport) -> list[tuple[str, str]]:
    """(status, line) per check - the line carries its non-color symbol."""
    return [(c.status, f"{_STATUS_SYMBOL[c.status]} {c.message}") for c in report.checks]


def primary_problem(config: LauncherConfig, report: DoctorReport) -> dict[str, str] | None:
    """The card for the most urgent finding that needs explaining, or None.

    Selection is by SEVERITY, not by a single status (#127). It used to scan
    for ``error`` only, which meant the security warning - the one place in the
    project that emits ``warn`` - had 22 explanation texts that could never be
    shown.

    The cheap fix would have been to let ``error`` also mean ``warn``. That
    smuggles a warning under a heading which says a problem was found: the user
    would read that something is broken while their app runs perfectly and is
    merely reachable from the network. Since this is the only ``warn`` emitter,
    that wording would have set the tone for every future warning. So the card
    carries a SEVERITY and the heading follows it.
    """
    for severity in _SEVERITY_ORDER:
        for check in report.checks:
            if check.status != severity:
                continue
            known = check.id in ERROR_CHECK_IDS
            return {
                "id": check.id,
                "severity": severity,
                "symbol": _STATUS_SYMBOL[severity],
                "title": i18n.t(_SEVERITY_TITLE_KEYS[severity], config),
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


# Long-running operations and their outcomes (#97). CONTRACT: every action
# listed here must leave the window in a DEFINED idle state for EVERY outcome
# - progress hidden and stopped, buttons re-enabled, next action startable
# without a restart. Coverage is enforced per (action x outcome) by
# tests/test_operation_end_states.py against the REAL windows; a new
# long-running action that is not listed here fails the suite's sync pin.
LONG_RUNNING_ACTIONS: tuple[str, ...] = (
    "install",
    "start",
    "update",
    "stop",
    "uninstall",
    "cleanup",
    "change_port",
    "change_internal_port",
)

# "cancel_unresponsive" is the exit of the cancelling state itself (#98):
# a cancel request the operation ignores (stuck syscall, hanging
# connection) must not leave the window in a forever-"cancelling" state -
# that would be the #97 class at a new spot. The watchdog forces this
# outcome after CANCEL_WATCHDOG_SECONDS with an honest message.
OPERATION_OUTCOMES: tuple[str, ...] = ("success", "failure", "cancelled", "cancel_unresponsive")

CANCEL_WATCHDOG_SECONDS = 10

# After a watchdog release the unresponsive operation may still be working
# on the SAME container/volume in the background (#100). Measured: a second
# parallel install fails with a raw engine 409; worse, a hung operation
# waking up AFTER an uninstall recreates resources - the reported end state
# would lie. So new long-running actions are BLOCKED while one is pending,
# with the guard's own exits: the late result clears it immediately, this
# TTL clears it otherwise (a stuck HTTP call has almost certainly died with
# its socket by then), and a launcher restart is named in the message.
PENDING_BACKGROUND_TTL_SECONDS = 600

# The honesty map (#98): which operations a cancel REALLY ends, what state
# the user is in afterwards, and what the next step is. Operations not
# listed are NOT cancellable and show no cancel control - stop/uninstall
# are short and a mid-flight abort could leave worse states than finishing;
# cleanup and port persistence are near-instant.
# action -> (post-state description key context, i18n key of the message the
# user sees on cancel; the per-mode backends add the kept-cache detail).
CANCELLABLE_ACTIONS: dict[str, str] = {
    # acquire/build phase is the long part; nothing runs afterwards, caches
    # kept, next step: press the same button again.
    "install": "operation_cancelled",
    "start": "operation_cancelled",
    # the tricky one: update STOPPED the app before re-acquiring - the user
    # ends up with a state they did not ask for; the message says so and
    # names Start as the next step (previous image still local, #88).
    "update": "update_cancelled_stopped",
    # same shape as update, one difference the message has to carry (#101):
    # the new internal port is ALREADY persisted when the rebuild starts, so
    # Start rebuilds with it - the user is not back where they began.
    "change_internal_port": "internal_port_cancelled_stopped",
}
# NOT cancellable, each with its reason (honesty over pretense - a control
# that only resets the UI while work continues is worse than none):
#   stop/uninstall - short, and a mid-flight abort could leave a worse
#     half-state (container removed, volume kept or vice versa) than
#     finishing; change_port/cleanup - near-instant (no build, no image
#     acquisition in any mode, #112);
#     archive load inside image mode - a single fast local call.


def check_pending_operation(config: LauncherConfig, action_id: str) -> tuple[str | None, str | None]:
    """The ONE concurrency gate both entry paths call (#102) - GUI and CLI;
    two implementations of the same guard would drift (mirror class of the
    bundle finding).

    Returns ``(block_message, expiry_note)``: a block message refuses the
    action (the marker's owner may still work on the same container); an
    expiry note lets the action proceed but says the previous operation
    NEVER confirmed its end - a release by time is not an all-clear.
    """
    import time as _time

    from docker_app_launcher import lockfile as _lockfile

    if action_id not in LONG_RUNNING_ACTIONS:
        return None, None
    marker, degraded = _lockfile.read_pending_operation(config)
    if degraded is not None:
        # DELIBERATE open with a visible note (#103): failing closed here
        # would brick the launcher; opening silently would hide that a
        # protection is missing.
        return None, i18n.t("guard_unavailable", config, detail=degraded)
    if marker is None:
        return None, None
    try:
        age = _time.time() - float(str(marker.get("at", 0)))
    except ValueError:
        age = PENDING_BACKGROUND_TTL_SECONDS
    if age >= PENDING_BACKGROUND_TTL_SECONDS:
        _lockfile.clear_pending_operation(config)  # the TTL is the guard's exit
        return None, i18n.t("pending_expired_unconfirmed", config, action=str(marker.get("action", "?")))
    return (
        i18n.t(
            "operation_pending_blocked",
            config,
            action=str(marker.get("action", "?")),
            minutes=PENDING_BACKGROUND_TTL_SECONDS // 60,
        ),
        None,
    )
