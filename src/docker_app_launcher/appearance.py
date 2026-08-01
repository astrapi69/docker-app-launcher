"""What appearance the SYSTEM asks for - light, dark, or nothing (#118).

Three-valued on purpose. Two values would force the "the system said nothing"
case onto one of the other two, and that is exactly how the library this
replaces fails: measured on a KDE desktop, ``darkdetect`` reports ``Light``
while the XDG portal and Qt both report dark, because it really answers "does
the GTK theme name contain -dark" rather than "does the user want a dark
appearance". A mechanism that runs reliably and answers a DIFFERENT question
than the one asked is the error class this module is written against.

Sources, one per platform, measured on all three (#118):

============  ===================================  ================
platform      source                               frozen-usable
============  ===================================  ================
Linux         XDG portal ``org.freedesktop.appearance color-scheme``  yes
macOS         ``defaults read -g AppleInterfaceStyle``                yes
Windows       ``AppsUseLightTheme`` under ``Personalize``             yes
============  ===================================  ================

All three are OS tools driven through stdlib, so they work from the frozen
bundle - which ships neither the ``ctk`` nor the ``qt`` extra, and therefore
could never carry a detection built on ``darkdetect`` or Qt no matter how
right those are.

Two platform facts worth knowing before reading the code:

* **macOS has only two observable cases.** ``AppleInterfaceStyle`` exists
  ONLY in dark mode; its absence is the documented representation of light,
  not of "never configured". Measured on macos-latest: rc=1, "The
  domain/default pair ... does not exist", with the machine set to light.
  So :data:`NO_PREFERENCE` is not reachable there, and inventing a third
  value for it would be a lie about the platform.
* **Windows keeps two separate values.** ``AppsUseLightTheme`` (what
  APPLICATIONS use) and ``SystemUsesLightTheme`` (what the shell uses) can
  differ. A launcher window follows the first; reading the other would be a
  confident answer to a question nobody asked.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

from docker_app_launcher.subprocess_utils import subprocess_kwargs

logger = logging.getLogger("docker_app_launcher.appearance")

LIGHT = "light"
DARK = "dark"
#: The system expressed no preference, or could not be asked. NEVER collapse
#: this into LIGHT inside the detection - see :func:`effective_appearance`,
#: which is the ONE readable place where the merge happens.
NO_PREFERENCE = "no_preference"

APPEARANCES = (LIGHT, DARK, NO_PREFERENCE)

#: What a config may ask for. ``system`` follows the detection.
CONFIG_APPEARANCES = ("system", LIGHT, DARK)

_PORTAL_ARGV = [
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.freedesktop.portal.Desktop",
    "--object-path",
    "/org/freedesktop/portal/desktop",
    "--method",
    "org.freedesktop.portal.Settings.Read",
    "org.freedesktop.appearance",
    "color-scheme",
]

_cached: tuple[str, str] | None = None


def reset_cache() -> None:
    """Forget the detected appearance (tests; a launcher restart otherwise)."""
    global _cached
    _cached = None


def _run(argv: list[str]) -> tuple[bool, int, str]:
    """``(tool_present, returncode, output)``.

    The first element is the whole point: "the tool is not here" and "the tool
    ran and found nothing" are different answers. Conflating them is how a
    probe reports a confident verdict about a platform it never asked - which
    happened to the measurement script for this very issue, reporting macOS as
    light while running on Linux.
    """
    if shutil.which(argv[0]) is None:
        return False, -1, f"{argv[0]} not present"
    try:
        # subprocess_kwargs: on Windows an unguarded call flashes a console
        # window - and Windows is exactly where this module runs 'reg'.
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False, **subprocess_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, -1, f"{type(exc).__name__}: {exc}"
    return True, done.returncode, (done.stdout or done.stderr).strip()


def _linux() -> tuple[str, str]:
    present, rc, out = _run(_PORTAL_ARGV)
    if not present:
        return NO_PREFERENCE, "no XDG portal client (gdbus) on this system"
    if rc != 0:
        return NO_PREFERENCE, f"XDG portal unreachable: {out}"
    if "uint32 1" in out:
        return DARK, "XDG portal color-scheme=1 (prefer dark)"
    if "uint32 2" in out:
        return LIGHT, "XDG portal color-scheme=2 (prefer light)"
    return NO_PREFERENCE, f"XDG portal color-scheme=0 (no preference): {out}"


def _macos() -> tuple[str, str]:
    present, rc, out = _run(["defaults", "read", "-g", "AppleInterfaceStyle"])
    if not present:
        return NO_PREFERENCE, "the 'defaults' tool is not present"
    if rc != 0:
        # The key exists ONLY in dark mode - its absence IS light on macOS.
        return LIGHT, "AppleInterfaceStyle absent, which is how macOS represents light"
    return (DARK if "dark" in out.lower() else LIGHT), f"AppleInterfaceStyle={out}"


def _windows() -> tuple[str, str]:
    present, rc, out = _run(
        [
            "reg",
            "query",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "/v",
            "AppsUseLightTheme",
        ]
    )
    if not present:
        return NO_PREFERENCE, "the 'reg' tool is not present"
    if rc != 0:
        # Windows writes this only once the user has touched the setting.
        return NO_PREFERENCE, "AppsUseLightTheme not set - never chosen, which is not the same as light"
    tail = out.split()[-1] if out.split() else ""
    if tail in ("0x0", "0"):
        return DARK, "AppsUseLightTheme=0 (dark)"
    if tail in ("0x1", "1"):
        return LIGHT, "AppsUseLightTheme=1 (light)"
    return NO_PREFERENCE, f"AppsUseLightTheme unreadable: {out}"


def detect_system_appearance() -> tuple[str, str]:
    """``(appearance, why)`` - one of :data:`APPEARANCES` plus the reason.

    The reason is not decoration. The defect this replaces went unnoticed
    precisely because a wrong answer with no trace looks like a design choice:
    a light window on a dark desktop reads as a decision, not as a bug.
    """
    global _cached
    if _cached is not None:
        return _cached
    if sys.platform == "darwin":
        verdict = _macos()
    elif sys.platform.startswith("win"):
        verdict = _windows()
    else:
        verdict = _linux()
    logger.info("system appearance: %s (%s)", verdict[0], verdict[1])
    _cached = verdict
    return verdict


def effective_appearance(configured: str) -> tuple[str, str]:
    """The appearance to RENDER: always ``light`` or ``dark``, plus the why.

    This is the ONE place where the three-valued detection is merged down to
    the two a renderer can use, and the merge is deliberately readable:

    * an explicit ``light``/``dark`` in the config always wins - nobody is
      trapped by a detection that reads their desktop wrongly;
    * ``no_preference`` resolves to ``light``, because that is what all three
      toolkits show untouched, so anything else would surprise a system that
      said nothing. A NAMED decision, not a silent fallback.
    """
    if configured in (LIGHT, DARK):
        return configured, f"config asked for {configured}"
    detected, why = detect_system_appearance()
    if detected == NO_PREFERENCE:
        return LIGHT, f"{why} -> falling back to light (documented default)"
    return detected, why
