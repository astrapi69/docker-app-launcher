"""The colours the launcher uses, by MEANING rather than by widget (#118).

The measurement that shaped this module: a dark mode looked like 54 widgets
of work, because that is how many carry their own background/foreground in the
rendered window. It is not. Tk's option database colours the whole classic
tree from two calls, and what actually needs deciding is far smaller - five
MEANINGS, spread over 20 hardcoded values in four files:

    #c5221f  x7   error
    #188038  x6   success
    #333333  x2   muted text
    #2a5db0  x2   link
    #b06000  x1   warning
    #555     x1   muted text  <- same meaning as #333333, different value
    #ffffff  x1   input background

That last pair is the argument for this module on its own: two values for one
meaning, which nobody notices without a palette and which cannot arise with
one.

The light palette reproduces today's appearance exactly, so introducing it
changes nothing visually. The dark variants are chosen for CONTRAST, not for
taste, and a test measures their WCAG ratio - because the accessibility
promise this project already makes (status is never signalled by colour alone,
there is always a ✓/✗/· symbol) must not be quietly undercut by a dark theme
whose red is unreadable on its own background.
"""

from __future__ import annotations

from dataclasses import dataclass

from docker_app_launcher.appearance import DARK, LIGHT


@dataclass(frozen=True)
class Palette:
    """One appearance, addressed by meaning.

    Renderers ask for ``palette.error``, never for ``"#c5221f"`` - which is
    what keeps a sixth meaning from being invented by accident, and what makes
    a second value for an existing meaning impossible.
    """

    #: Window and frame background.
    background: str
    #: Default text.
    foreground: str
    #: Text entry / log panel background (lighter than the window in light mode,
    #: darker in dark mode - it is a surface, not a state).
    field_background: str
    #: Secondary text: hints, versions, paths. Never a state.
    muted: str
    #: A state: the thing worked.
    success: str
    #: A state: the thing failed.
    error: str
    #: A state: it worked but something needs attention.
    warning: str
    #: Something to click or open.
    link: str
    #: Button face - a surface, not a state.
    button_background: str
    #: Button face while pressed/hovered.
    button_active_background: str
    #: Text of a DISABLED control. Deliberately low contrast: that is what
    #: "you cannot use this now" looks like, and WCAG 1.4.3 exempts inactive
    #: controls from the ratio. Excluded from TEXT_MEANINGS for that reason,
    #: with the reason written down rather than left as an omission.
    disabled_foreground: str


# Exactly today's values, so this module lands with no visual change at all.
LIGHT_PALETTE = Palette(
    background="#d9d9d9",
    foreground="#202124",
    field_background="#ffffff",
    muted="#333333",
    success="#188038",
    error="#c5221f",
    warning="#b06000",
    link="#2a5db0",
    button_background="#e3e3e3",
    button_active_background="#cccccc",
    disabled_foreground="#8a8a8a",
)

# Contrast-matched counterparts. Not taste: each foreground colour is measured
# against DARK_PALETTE.background in tests/test_palette.py and must clear the
# WCAG AA threshold for body text.
DARK_PALETTE = Palette(
    background="#1e1e1e",
    foreground="#e8e8e8",
    field_background="#2d2d2d",
    muted="#b0b4b8",
    success="#81c995",
    error="#f28b82",
    warning="#fdd663",
    link="#8ab4f8",
    button_background="#333333",
    button_active_background="#444444",
    disabled_foreground="#777777",
)

_BY_APPEARANCE = {LIGHT: LIGHT_PALETTE, DARK: DARK_PALETTE}

#: The meanings a renderer may ask for. A sync pin in the tests keeps this in
#: step with the dataclass, so a new field cannot ship unmeasured for contrast.
MEANINGS: tuple[str, ...] = (
    "background",
    "foreground",
    "field_background",
    "muted",
    "success",
    "error",
    "warning",
    "link",
    "button_background",
    "button_active_background",
    "disabled_foreground",
)

#: Foreground meanings: measured against ``background`` for contrast. The two
#: surface colours are excluded because a background has no ratio with itself.
TEXT_MEANINGS: tuple[str, ...] = ("foreground", "muted", "success", "error", "warning", "link")


def palette_for(appearance: str) -> Palette:
    """The palette for ``light`` or ``dark``.

    Deliberately NOT accepting ``no_preference``: the merge from three values
    to two happens once, in ``appearance.effective_appearance``, and a second
    place that quietly maps the unknown case would be exactly the drift this
    whole issue is about.
    """
    try:
        return _BY_APPEARANCE[appearance]
    except KeyError:
        raise ValueError(
            f"palette_for expects {LIGHT!r} or {DARK!r}, got {appearance!r} - "
            "resolve it through appearance.effective_appearance first"
        ) from None
