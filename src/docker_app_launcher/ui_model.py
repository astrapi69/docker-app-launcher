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

from docker_app_launcher import actions, i18n
from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.ui_model")

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
SECONDARY_BUTTONS = ["cleanup", "background"]

# button name -> i18n label key.
BUTTON_LABELS = {
    "install": "install",
    "start": "start",
    "open_browser": "open_browser",
    "stop": "stop",
    "uninstall": "uninstall",
    "copy_log": "log_copy",
    "cleanup": "cleanup",
    "background": "run_in_background",
    "apply_port": "apply_port",
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
        "background": False,
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
        "background": False,
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
        "background": False,
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
        "background": True,
    },
}


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
) -> tuple[bool, str] | None:
    """Run the action for ``action_id`` through the actions layer.

    Returns ``(ok, message)`` for actions that report a result, or ``None`` for
    fire-and-forget ids (open, recheck). ``port`` is only consumed by
    ``change_port`` (the in-place host-port change); ``on_progress`` by the
    install/start build phases. Pure (no widget toolkit) so it is unit-testable
    by mocking ``actions``.
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


def should_keep_alive_on_close(state: str, *, minimize_enabled: bool) -> bool:
    """Whether the X button should keep the launcher alive instead of quitting.

    True while the app is RUNNING and the app opts in (``minimize_enabled``):
    the window then goes to the tray, or - when the tray is unavailable - is
    minimized to the taskbar (never silently killed). When the app is not
    running, or the app opted out, the X quits the launcher.
    """
    return state == "running" and minimize_enabled
