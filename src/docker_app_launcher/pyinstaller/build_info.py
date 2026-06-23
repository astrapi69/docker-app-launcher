"""PyInstaller build helpers: hidden imports + build-time version injection.

A frozen (PyInstaller) build cannot rely on ``importlib.metadata`` to know
its version, so the spec writes a generated ``_build_info.py`` into the app
at build time and the runtime reads it back. These helpers are pure and
unit-testable; the actual ``.spec`` lives next to this module as
``launcher.spec.template``.
"""

from __future__ import annotations

from pathlib import Path

_BUILD_INFO_TEMPLATE = '"""Generated at build time - do not edit."""\n\n__build_version__ = "{version}"\n'


def hidden_imports() -> list[str]:
    """PyInstaller ``hiddenimports`` needed to freeze a launcher app.

    Lists every ``docker_app_launcher`` submodule that is imported lazily
    (so PyInstaller's static analysis would otherwise miss it). Apps may
    extend this list (e.g. with ``pystray`` tray backends).
    """
    return [
        "docker_app_launcher",
        "docker_app_launcher.actions",
        "docker_app_launcher.config",
        "docker_app_launcher.gui",
        "docker_app_launcher.i18n",
        "docker_app_launcher.lockfile",
        "docker_app_launcher.logging_setup",
        "docker_app_launcher.tray",
        "docker_app_launcher.update_check",
    ]


def write_build_info(dest: Path, version: str) -> None:
    """Write a generated ``_build_info.py`` carrying the frozen build version.

    Call this from a PyInstaller ``.spec`` before ``Analysis(...)`` so the
    version is baked into the executable.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_BUILD_INFO_TEMPLATE.format(version=version), encoding="utf-8")


def read_build_info(path: Path) -> str | None:
    """Read the ``__build_version__`` from a generated build-info file.

    Returns ``None`` when the file is absent or malformed (a source checkout
    that was never frozen), so callers fall back to ``importlib.metadata``.
    """
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("__build_version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None
