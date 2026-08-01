"""Put a :class:`~docker_app_launcher.palette.Palette` onto a real window (#118).

Lifted from the colouring helper that already lived in the test suite
(``tests/test_gui_window.apply_dark_theme``, which produced the dark screenshots
attached to every CI run). Building a second one beside it would have been the
drift this issue is about; the test helper now calls THIS function, so there is
one mechanism and the screenshots keep showing what users get.

Two mechanisms are needed together, and measuring said why:

* ``option_add`` sets a widget CLASS default and colours everything created
  AFTERWARDS from two calls - measured on a real root: Frame, Label, Button,
  Entry, Text and Scrollbar all inherit. It cannot touch what already exists.
* the recursive pass colours what is ALREADY there.

The test helper needed the second one only, and paid for it by having to be
re-called before every screenshot because widgets built later (the docker-help
panel, the cleanup offer) came out light. With both, a window is styled once
and stays styled.

ttk widgets (Combobox, Progressbar, Separator) have no ``bg``/``fg`` and are
handled through ``ttk.Style`` instead. CustomTkinter styles itself and is
skipped deliberately - it gets the appearance through its own
``set_appearance_mode``, fed from OUR detection.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from typing import Any

from docker_app_launcher.palette import Palette

logger = logging.getLogger("docker_app_launcher.theming")


def arm_widget_defaults(root: tk.Misc, palette: Palette) -> None:
    """Colour every widget created FROM NOW ON, via Tk's option database.

    Must run before the widgets are built - the database is consulted at
    creation time, and it is per Tk ROOT (a call on a different root silently
    does nothing, which is how the first measurement for this issue produced
    "0 of 41 reached" and looked like a clean no).
    """
    with contextlib.suppress(tk.TclError):
        root.option_add("*Background", palette.background)
        root.option_add("*Foreground", palette.foreground)
        root.option_add("*Entry.background", palette.field_background)
        root.option_add("*Text.background", palette.field_background)
        root.option_add("*Button.background", palette.button_background)
        root.option_add("*Button.activeBackground", palette.button_active_background)
        root.option_add("*disabledForeground", palette.disabled_foreground)


def apply_palette(root: tk.Misc, palette: Palette) -> None:
    """Colour ``root`` and everything already under it.

    Best-effort by design: a widget that refuses an option is skipped rather
    than raising. A window that renders in the wrong colours is a blemish; a
    window that fails to open is an outage.
    """
    _style_ttk(root, palette)
    with contextlib.suppress(tk.TclError):
        root.configure(bg=palette.background)  # type: ignore[call-arg]

    stack: list[tk.Misc] = list(root.winfo_children())
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if not type(widget).__module__.startswith("tkinter"):
            continue  # CustomTkinter and friends style themselves
        _style_widget(widget, palette)


def _style_ttk(root: tk.Misc, palette: Palette) -> None:
    try:
        from tkinter import ttk

        style = ttk.Style(root)
        # 'clam' is the one built-in theme that actually honours background
        # colours on Linux; the default theme ignores them.
        style.theme_use("clam")
        style.configure(
            ".", background=palette.background, foreground=palette.foreground, fieldbackground=palette.field_background
        )
        style.map("TCombobox", fieldbackground=[("readonly", palette.field_background)])
    except tk.TclError as exc:  # pragma: no cover - platform dependent
        logger.debug("ttk styling unavailable: %s", exc)


def _style_widget(widget: tk.Misc, palette: Palette) -> None:
    try:
        if isinstance(widget, tk.Button):
            widget.configure(
                bg=palette.button_background,
                fg=palette.foreground,
                activebackground=palette.button_active_background,
                activeforeground=palette.foreground,
                disabledforeground=palette.disabled_foreground,
            )
        elif isinstance(widget, tk.Entry):
            widget.configure(
                bg=palette.field_background,
                fg=palette.foreground,
                insertbackground=palette.foreground,
                disabledbackground=palette.background,
            )
        elif isinstance(widget, tk.Text):
            widget.configure(bg=palette.field_background, fg=palette.foreground, insertbackground=palette.foreground)
        elif isinstance(widget, tk.Frame | tk.Label):
            widget.configure(bg=palette.background)
            if isinstance(widget, tk.Label):
                widget.configure(fg=palette.foreground)
    except tk.TclError:
        # ttk widgets and platform quirks have no bg/fg options - skip them;
        # _style_ttk covers those.
        pass


def qt_palette(palette: Palette) -> Any:
    """A ``QPalette`` carrying the same meanings, for the Qt frontend.

    Qt has its own palette system, so the mapping happens here rather than by
    colouring widgets one by one - the same reason ttk goes through ``Style``.
    Returns ``None`` when PySide6 is absent, so callers need no import guard.
    """
    try:
        from PySide6.QtGui import QColor, QPalette
    except ImportError:  # pragma: no cover - only without the qt extra
        return None

    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(palette.background))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(palette.foreground))
    qp.setColor(QPalette.ColorRole.Base, QColor(palette.field_background))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(palette.background))
    qp.setColor(QPalette.ColorRole.Text, QColor(palette.foreground))
    qp.setColor(QPalette.ColorRole.Button, QColor(palette.button_background))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(palette.foreground))
    qp.setColor(QPalette.ColorRole.Link, QColor(palette.link))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette.field_background))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(palette.foreground))
    qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(palette.disabled_foreground))
    qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(palette.disabled_foreground))
    return qp
