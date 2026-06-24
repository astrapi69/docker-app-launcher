"""Tiny YAML-backed i18n for the launcher.

Strings live in per-language YAML files next to this module (``de.yaml``,
``en.yaml``, ...) - a flat ``key: text`` map per file. They are loaded once at
import into :data:`STRINGS` (a ``{lang: {key: text}}`` table). :func:`t`
resolves a key for the active config locale and interpolates ``{app}`` (the
configured app name) plus any keyword arguments.

Resolution order for one key:

1. ``config.custom_strings[locale][key]`` - user override for this app.
2. ``STRINGS[locale][key]`` - built-in translation.
3. ``STRINGS["en"][key]`` - English fallback.
4. the key itself - so a missing string is visible, never a crash.

Add a language by dropping a ``<code>.yaml`` file beside this module; an app can
override or add individual strings through ``LauncherConfig.custom_strings``.
Keys are deliberately FLAT (no nesting) so every existing ``t("key", ...)`` call
site is unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.i18n")

FALLBACK_LANG = "en"
_I18N_DIR = Path(__file__).resolve().parent


def _load_all() -> dict[str, dict[str, str]]:
    """Load every ``<lang>.yaml`` beside this module into a flat catalog.

    A file that is missing, unreadable, or malformed is skipped with a warning
    rather than crashing the launcher - the affected language simply falls back
    to English (or the key itself).
    """
    catalog: dict[str, dict[str, str]] = {}
    for path in sorted(_I18N_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - defensive
            logger.warning("could not load i18n file %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            catalog[path.stem] = {str(key): str(value) for key, value in data.items()}
    return catalog


STRINGS: dict[str, dict[str, str]] = _load_all()


def t(key: str, config: LauncherConfig, **kwargs: Any) -> str:
    """Translate ``key`` for ``config.locale``; interpolate ``{app}`` + kwargs.

    Custom strings (``config.custom_strings``) take precedence over the built-in
    catalog. Missing keys fall back to English and finally to the key itself. A
    bad format placeholder is logged and the raw template returned rather than
    raising.
    """
    locale = config.locale
    template = (
        config.custom_strings.get(locale, {}).get(key)
        or STRINGS.get(locale, {}).get(key)
        or STRINGS.get(FALLBACK_LANG, {}).get(key, key)
    )
    params: dict[str, Any] = {"app": config.app_name, **kwargs}
    try:
        return template.format(**params)
    except (KeyError, IndexError) as exc:
        logger.warning("i18n format failed for %r: %s", key, exc)
        return template


def available_languages() -> list[str]:
    """Sorted list of built-in language codes (one per ``<code>.yaml``)."""
    return sorted(STRINGS.keys())
