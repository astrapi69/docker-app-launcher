"""A hover tooltip for one Tk widget, with dynamically settable text.

Used by the Tk and CustomTkinter frontends to explain WHY a disabled button
is disabled; an empty text disables the tooltip, so one instance stays
attached for the widget's whole life.
"""

from __future__ import annotations

import logging
import tkinter as tk

from docker_app_launcher.palette import LIGHT_PALETTE, Palette

logger = logging.getLogger("docker_app_launcher.frontends.tooltip")


class Tooltip:
    """A hover tooltip for one widget, with dynamically settable text.

    The tooltip only appears while the text is non-empty, so the same instance
    can be attached to a button once and switched on (disabled, with a reason)
    or off (enabled) as the app state changes.
    """

    def __init__(self, widget: tk.Widget, palette: Palette = LIGHT_PALETTE) -> None:
        # Defaulted, so the long-standing one-argument call sites keep working;
        # the windows pass their ACTIVE palette (#118), which is what makes a
        # tooltip readable in dark mode instead of dark-on-dark.
        self._palette = palette
        self._widget = widget
        self._text = ""
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        """Set the tooltip text; an empty string disables the tooltip."""
        self._text = text or ""
        if not self._text:
            self._hide()

    def _show(self, _event: object = None) -> None:
        if not self._text or self._tip is not None:
            return
        try:
            x = self._widget.winfo_rootx() + 12
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
            self._tip = tk.Toplevel(self._widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            tk.Label(
                self._tip,
                text=self._text,
                justify="left",
                background=self._palette.muted,
                foreground=self._palette.foreground,
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=3,
                font=("Segoe UI", 8),
            ).pack()
        except tk.TclError as exc:  # pragma: no cover - WM dependent
            logger.debug("could not show tooltip: %s", exc)
            self._tip = None

    def _hide(self, _event: object = None) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError as exc:  # pragma: no cover - WM dependent
                logger.debug("could not hide tooltip: %s", exc)
            self._tip = None
