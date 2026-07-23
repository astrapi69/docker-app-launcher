"""Frontend registry: resolve a GUI backend by name.

A frontend is any module exposing ``run(config, *, debug=False) -> int``.
Built-in: ``tk`` (the default tkinter window). Third-party packages can
register additional frontends via the ``docker_app_launcher.frontends``
entry-point group:

    [project.entry-points."docker_app_launcher.frontends"]
    qt = "my_package.qt_frontend"

and are then selectable with ``"gui_backend": "qt"`` in the launcher JSON
(or ``LauncherConfig(gui_backend="qt")``). The frontend renders the shared
:mod:`docker_app_launcher.ui_model` tables, so behaviour is identical across
backends by construction.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from types import ModuleType

ENTRY_POINT_GROUP = "docker_app_launcher.frontends"

# name -> module path of the frontends that ship with this package.
BUILTIN_FRONTENDS = {
    "tk": "docker_app_launcher.gui",
    "ctk": "docker_app_launcher.frontends.ctk",
    "qt": "docker_app_launcher.frontends.qt",
}


def available_frontends() -> list[str]:
    """All selectable frontend names: built-ins plus installed entry points."""
    names = set(BUILTIN_FRONTENDS)
    names.update(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
    return sorted(names)


def get_frontend(name: str) -> ModuleType:
    """Resolve ``name`` to a frontend module exposing ``run(config, *, debug)``.

    Built-ins win over entry points of the same name. Raises ``ValueError``
    with the list of known names when ``name`` matches neither, and
    ``TypeError`` when the resolved module has no callable ``run``.
    """
    if name in BUILTIN_FRONTENDS:
        module = importlib.import_module(BUILTIN_FRONTENDS[name])
    else:
        matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
        if not matches:
            known = ", ".join(available_frontends())
            raise ValueError(f"unknown gui_backend {name!r} (known: {known})")
        module = matches[0].load()
    if not callable(getattr(module, "run", None)):
        raise TypeError(f"frontend {name!r} ({module.__name__}) has no callable run(config, *, debug)")
    return module
