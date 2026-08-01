#!/usr/bin/env python3
"""MEASUREMENT for #118: what does each appearance source answer, per platform?

Not the feature. This script exists to turn three open questions into three
numbers before anything is built:

1. macOS - does ``defaults read -g AppleInterfaceStyle`` answer, and how?
2. Windows - does the ``AppsUseLightTheme`` registry value answer, and how?
3. What happens where NO source answers? The required result there is "no
   preference", never "light" - a wrong "light" is exactly the defect already
   measured in ``darkdetect`` on a KDE desktop, where it reported Light while
   the XDG portal and Qt both said dark, because it really answers "does the
   GTK theme name contain -dark".

Stdlib only, prints one line per source, never raises. Run it anywhere:

    python tests/measure_appearance_sources.py
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys

# The three answers a source may give. "unknown" is a first-class result and
# must stay distinguishable from "light" - collapsing them is the bug class
# this whole measurement is chasing.
DARK, LIGHT, UNKNOWN = "dark", "light", "unknown"


# Returned instead of a real exit code when the TOOL ITSELF is not there.
# Kept distinct on purpose: "the tool is missing" and "the tool ran and found
# nothing" are different answers, and conflating them is how a probe reports a
# confident "light" about a platform it never even asked. Measured: the first
# run of this very script reported macOS as "light" ON LINUX, because
# ``defaults`` does not exist there and every non-zero code was read as "key
# absent, therefore light" - the exact defect the script was written to find.
TOOL_MISSING = -1


def _run(argv: list[str]) -> tuple[int, str]:
    if shutil.which(argv[0]) is None:
        return TOOL_MISSING, f"{argv[0]} not present on this system"
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return TOOL_MISSING, f"{type(exc).__name__}: {exc}"
    return done.returncode, (done.stdout or done.stderr).strip()


def xdg_portal() -> tuple[str, str]:
    """freedesktop: 0 = no preference, 1 = prefer dark, 2 = prefer light."""
    rc, out = _run(
        [
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
    )
    if rc != 0:
        return UNKNOWN, f"portal unreachable: {out}"
    if "uint32 1" in out:
        return DARK, out
    if "uint32 2" in out:
        return LIGHT, out
    return UNKNOWN, f"{out} (0 = no preference)"


def gsettings_color_scheme() -> tuple[str, str]:
    rc, out = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    if rc != 0 or not out:
        return UNKNOWN, out or "no answer"
    if "dark" in out.lower():
        return DARK, out
    if "light" in out.lower():
        return LIGHT, out
    return UNKNOWN, out


def macos_interface_style() -> tuple[str, str]:
    """``AppleInterfaceStyle`` exists ONLY in dark mode - its absence is light,
    which is why the return code has to be read, not just the output."""
    rc, out = _run(["defaults", "read", "-g", "AppleInterfaceStyle"])
    if rc == TOOL_MISSING:
        # Not macOS at all - "unknown", never "light".
        return UNKNOWN, out
    if rc != 0:
        return LIGHT, f"key absent (rc={rc}) - on macOS that genuinely means light: {out}"
    return (DARK if "dark" in out.lower() else LIGHT), out


def _windows_personalize(value: str) -> tuple[str, str]:
    """One value under Personalize. 0 = dark, 1 = light, absent = never set.

    Absent must stay UNKNOWN: Windows writes these values only once the user
    has touched the setting, so "no value" means "never chosen", not "light".
    """
    rc, out = _run(["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "/v", value])
    if rc == TOOL_MISSING:
        return UNKNOWN, out
    if rc != 0:
        return UNKNOWN, f"value absent - never set by the user, NOT light: {out}"
    tail = out.split()[-1] if out.split() else ""
    flat = out.replace("\n", " | ")
    if tail in ("0x0", "0"):
        return DARK, flat
    if tail in ("0x1", "1"):
        return LIGHT, flat
    return UNKNOWN, flat


def windows_apps_use_light_theme() -> tuple[str, str]:
    """What APPLICATIONS should use - this is the one a launcher window follows."""
    return _windows_personalize("AppsUseLightTheme")


def windows_system_uses_light_theme() -> tuple[str, str]:
    """What the SHELL (taskbar, start menu) uses - separate, and NOT ours.

    Measured as its own line because the two can differ: Windows lets the user
    pick a dark shell with light apps and vice versa. Reading the wrong one
    would produce a confident answer to a question nobody asked - the defect
    class this whole measurement is about.
    """
    return _windows_personalize("SystemUsesLightTheme")


def darkdetect_theme() -> tuple[str, str]:
    try:
        import darkdetect
    except ImportError:
        return UNKNOWN, "not installed (it ships only with the ctk extra)"
    answer = darkdetect.theme()
    if answer is None:
        return UNKNOWN, "None"
    return (DARK if answer.lower() == "dark" else LIGHT), answer


def qt_colour_scheme() -> tuple[str, str]:
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return UNKNOWN, "PySide6 not installed (the qt extra)"
    try:
        QApplication.instance() or QApplication([])
        hints = QGuiApplication.styleHints()
        scheme = getattr(hints, "colorScheme", None)
        if scheme is None:
            return UNKNOWN, "colorScheme() needs Qt 6.5+"
        name = str(scheme())
        return (DARK if "Dark" in name else LIGHT if "Light" in name else UNKNOWN), name
    except Exception as exc:  # noqa: BLE001 - a measurement never raises
        return UNKNOWN, f"{type(exc).__name__}: {exc}"


# Per source: does it survive FREEZING? A detection that only works from a
# source checkout does not help the main path, which ships as a PyInstaller
# bundle without the ctk/qt extras.
_SOURCES = {
    "xdg_portal (linux)": (xdg_portal, "frozen-safe: stdlib + gdbus from the OS"),
    "gsettings color-scheme (linux)": (gsettings_color_scheme, "frozen-safe: stdlib + gsettings from the OS"),
    "macos AppleInterfaceStyle": (macos_interface_style, "frozen-safe: stdlib + defaults from the OS"),
    "windows AppsUseLightTheme": (windows_apps_use_light_theme, "frozen-safe: stdlib + reg from the OS"),
    "windows SystemUsesLightTheme": (windows_system_uses_light_theme, "frozen-safe: stdlib + reg from the OS"),
    "darkdetect (via ctk)": (darkdetect_theme, "NOT frozen-safe: python package, ships only with the ctk extra"),
    "qt styleHints": (qt_colour_scheme, "NOT frozen-safe: python package, ships only with the qt extra"),
}


def main() -> int:
    print(f"platform: {platform.system()} {platform.release()} | python {sys.version.split()[0]}")
    for name, (probe, frozen) in _SOURCES.items():
        verdict, detail = probe()
        print(f"  {name:<32} {verdict:<8} {detail}")
        print(f"  {'':<32} {'':<8} [{frozen}]")
    print("\nRequired reading: 'unknown' must never be reported as 'light' (#118).")
    print("Three-valued on purpose: light / dark / unknown. Two values force the")
    print("unknown case onto one of the others - which is how darkdetect fails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
