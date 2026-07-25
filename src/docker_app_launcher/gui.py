"""FACADE - kept for backward compatibility, the code lives elsewhere.

- The Tk window (:class:`LauncherApp`) moved to :mod:`docker_app_launcher.frontends.tk_window`
  (one frontend per file, next to ``ctk`` and ``qt``).
- The framework-neutral behaviour tables live in :mod:`docker_app_launcher.ui_model`.

Import from those modules in new code; this module only re-exports.
"""

from __future__ import annotations

from docker_app_launcher.frontends.tk_window import (
    LauncherApp as LauncherApp,
)
from docker_app_launcher.frontends.tk_window import (
    _set_window_icon as _set_window_icon,
)
from docker_app_launcher.frontends.tk_window import (
    run as run,
)
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
    internal_port_fields as internal_port_fields,
)
from docker_app_launcher.ui_model import (
    issue_tracker_url as issue_tracker_url,
)
from docker_app_launcher.ui_model import (
    port_editable as port_editable,
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
