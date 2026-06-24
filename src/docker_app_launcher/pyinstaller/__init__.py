"""PyInstaller integration for docker-app-launcher applications.

Ships a ready-to-fill ``.spec`` template plus the :mod:`build_info` helpers
(hidden imports + build-time version injection). A downstream app renders the
template with its own values::

    from docker_app_launcher.pyinstaller import render_spec

    spec_text = render_spec(
        app_slug="my-app",
        entry_script="run_launcher.py",
        icon_path="my-app.png",
        config_json="launcher.json",
    )

Markers in the template use ``{{NAME}}`` so the file stays readable and never
clashes with the Python braces in the spec body.
"""

from __future__ import annotations

from pathlib import Path

from docker_app_launcher.pyinstaller.build_info import (
    hidden_imports,
    read_build_info,
    write_build_info,
)

__all__ = [
    "hidden_imports",
    "read_build_info",
    "render_spec",
    "spec_template_path",
    "write_build_info",
]

_REQUIRED_FIELDS = ("app_slug", "entry_script", "icon_path", "config_json")


def spec_template_path() -> Path:
    """Absolute path of the bundled ``launcher.spec.template``."""
    return Path(__file__).resolve().parent / "launcher.spec.template"


def render_spec(*, app_slug: str, entry_script: str, icon_path: str, config_json: str) -> str:
    """Render the bundled spec template into a concrete ``.spec`` string.

    All four fields are required; raises :class:`ValueError` if a marker is
    left unrendered (a typo-proofing guard).
    """
    text = spec_template_path().read_text(encoding="utf-8")
    replacements = {
        "APP_SLUG": app_slug,
        "ENTRY_SCRIPT": entry_script,
        "ICON_PATH": icon_path,
        "CONFIG_JSON": config_json,
    }
    for marker, value in replacements.items():
        text = text.replace("{{" + marker + "}}", value)
    if "{{" in text:
        raise ValueError("unrendered marker(s) remain in the spec template")
    return text
