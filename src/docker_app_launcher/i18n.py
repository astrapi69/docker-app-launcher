"""Tiny, dependency-free i18n for the launcher.

Strings live in the in-module :data:`STRINGS` table, keyed by language then by
message key. :func:`t` resolves a key for the active config locale and
interpolates ``{app}`` (the configured app name) plus any keyword arguments.

Resolution order for one key:

1. ``config.custom_strings[locale][key]`` - user override for this app.
2. ``STRINGS[locale][key]`` - built-in translation.
3. ``STRINGS["en"][key]`` - English fallback.
4. the key itself - so a missing string is visible, never a crash.

New languages are added by extending :data:`STRINGS`; an app can override or
add individual strings through ``LauncherConfig.custom_strings`` without
touching this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.i18n")

FALLBACK_LANG = "en"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # --- state headings ---
        "not_installed": "{app} is not installed.",
        "running": "{app} is running on port {port}.",
        "stopped": "{app} is installed but stopped.",
        "no_docker": "Docker is not started.",
        # --- buttons ---
        "install": "Install",
        "start": "Start",
        "stop": "Stop",
        "uninstall": "Uninstall",
        "open_browser": "Open in browser",
        "retry": "Check again",
        "cleanup": "Clean up",
        "skip": "Skip",
        "tray_open": "Open",
        "tray_quit": "Quit",
        # --- progress / steps ---
        "checking_docker": "Checking Docker...",
        "docker_ok": "Docker is running ✓",
        "installing": "{app} is being set up...",
        "building": "Building image (may take a few minutes the first time)...",
        "image_built": "Image built ✓",
        "starting": "Starting container...",
        "container_started": "Container started ✓",
        "updating": "Updating image (a few minutes after code changes, seconds otherwise)...",
        "checking_health": "Checking readiness...",
        "health_ok": "Readiness confirmed ✓",
        "uninstalling": "Uninstall started...",
        "no_containers": "No containers found ✓",
        "verify_clean": "Verification: no containers found ✓",
        "verify_remain": "Verification: {count} container(s) remain ✗",
        # --- results ---
        "ready": "Installation complete. {app} is ready.",
        "already_installed": "{app} is already installed and running.",
        "already_running": "{app} is already running.",
        "start_done": "{app} started.",
        "stop_done": "{app} stopped.",
        "already_stopped": "{app} was already stopped.",
        "uninstall_done": "Uninstall complete. Your data is preserved.",
        "nothing_to_uninstall": "Nothing to uninstall (no container present).",
        "confirm_uninstall": "Do you want to uninstall {app}?\nYour data is preserved.",
        # --- cleanup ---
        "cleanup_found": "Earlier installation leftovers found:",
        "cleanup_running": "Cleaning up...",
        "cleanup_done": "Cleanup complete: {count} artifact(s) removed, {freed} freed.",
        "cleanup_partial": "Cleanup partially failed ({count} step(s)).",
        "cleanup_skipped": "Cleanup skipped.",
        "data_preserved": "Your data was preserved.",
        "step_stop_container": "Stop container '{name}'",
        "step_remove_container": "Remove container '{name}'",
        "step_remove_image": "Remove image '{ref}'",
        "step_remove_volume": "Remove volume '{name}'",
        "step_remove_config": "Remove config '{path}'",
        "scan_containers": "Searching orphaned containers... {count} found",
        "scan_images": "Searching stale images... {count} found",
        "scan_volumes": "Searching orphaned volumes... {count} found",
        "scan_configs": "Searching config leftovers... {count} found",
        # --- ports ---
        "port_free": "Port {port} is free.",
        "port_occupied": "Port {port} is occupied.",
        "port_invalid": "Port must be between {min} and {max}.",
        "port_set": "Port set to {port}.",
        "no_free_port": "No free port found.",
        "free_port_found": "Free port found: {port}.",
        # --- docker / errors ---
        "docker_unavailable": "Docker is not available (not started).",
        "docker_not_installed": "Docker is not installed (docker not in PATH).",
        "docker_no_response": "Docker is not responding.",
        "docker_running": "Docker is running.",
        "docker_stopped": "Docker is not started.",
        "compose_not_found": "Compose file not found: {path}",
        "build_failed": "Docker build failed:\n{detail}",
        "build_timeout": "Docker build timed out.",
        "start_failed": "Start failed:\n{detail}",
        "start_timeout": "Start timed out.",
        "not_reachable": "Installed, but {app} is not reachable: {detail}",
        "container_not_running": "Container was built but is not running.",
        "start_no_container": "Start command ran, but no container is running.",
        "stop_failed": "Stop failed: {detail}",
        "stop_not_verified": "Container still running after the stop command.",
        "uninstall_partial": "Partially removed: {count} container(s) could not be removed.",
        "not_reachable_after": "{app} not reachable after {timeout}s ({detail}).",
        "error": "Error: {msg}",
        "error_word": "Error",
    },
    "de": {
        # --- state headings ---
        "not_installed": "{app} ist nicht installiert.",
        "running": "{app} laeuft auf Port {port}.",
        "stopped": "{app} ist installiert, aber gestoppt.",
        "no_docker": "Docker ist nicht gestartet.",
        # --- buttons ---
        "install": "Installieren",
        "start": "Starten",
        "stop": "Stoppen",
        "uninstall": "Deinstallieren",
        "open_browser": "Im Browser oeffnen",
        "retry": "Erneut pruefen",
        "cleanup": "Aufraeumen",
        "skip": "Ueberspringen",
        "tray_open": "Oeffnen",
        "tray_quit": "Beenden",
        # --- progress / steps ---
        "checking_docker": "Docker pruefen...",
        "docker_ok": "Docker laeuft ✓",
        "installing": "{app} wird eingerichtet...",
        "building": "Image bauen (kann beim ersten Mal einige Minuten dauern)...",
        "image_built": "Image gebaut ✓",
        "starting": "Container starten...",
        "container_started": "Container gestartet ✓",
        "updating": "Image wird aktualisiert (nach Code-Aenderungen einige Minuten, sonst Sekunden)...",
        "checking_health": "Bereitschaft pruefen...",
        "health_ok": "Bereitschaft bestaetigt ✓",
        "uninstalling": "Deinstallation gestartet...",
        "no_containers": "Keine Container gefunden ✓",
        "verify_clean": "Verifizierung: keine Container gefunden ✓",
        "verify_remain": "Verifizierung: {count} Container verbleiben ✗",
        # --- results ---
        "ready": "Installation abgeschlossen. {app} ist bereit.",
        "already_installed": "{app} ist bereits installiert und laeuft.",
        "already_running": "{app} laeuft bereits.",
        "start_done": "{app} gestartet.",
        "stop_done": "{app} gestoppt.",
        "already_stopped": "{app} war bereits gestoppt.",
        "uninstall_done": "Deinstallation abgeschlossen. Deine Daten bleiben erhalten.",
        "nothing_to_uninstall": "Nichts zu deinstallieren (kein Container vorhanden).",
        "confirm_uninstall": "Moechtest du {app} deinstallieren?\nDeine Daten bleiben erhalten.",
        # --- cleanup ---
        "cleanup_found": "Fruehere Installationsreste gefunden:",
        "cleanup_running": "Aufraeumen...",
        "cleanup_done": "Aufraeumen abgeschlossen: {count} Artefakt(e) entfernt, {freed} freigegeben.",
        "cleanup_partial": "Aufraeumen teilweise fehlgeschlagen ({count} Schritt(e)).",
        "cleanup_skipped": "Aufraeumen uebersprungen.",
        "data_preserved": "Deine Daten wurden beibehalten.",
        "step_stop_container": "Container '{name}' stoppen",
        "step_remove_container": "Container '{name}' entfernen",
        "step_remove_image": "Image '{ref}' entfernen",
        "step_remove_volume": "Volume '{name}' entfernen",
        "step_remove_config": "Konfiguration '{path}' entfernen",
        "scan_containers": "Suche verwaiste Container... {count} gefunden",
        "scan_images": "Suche veraltete Images... {count} gefunden",
        "scan_volumes": "Suche verwaiste Volumes... {count} gefunden",
        "scan_configs": "Suche Config-Reste... {count} gefunden",
        # --- ports ---
        "port_free": "Port {port} ist frei.",
        "port_occupied": "Port {port} ist belegt.",
        "port_invalid": "Port muss zwischen {min} und {max} liegen.",
        "port_set": "Port auf {port} gesetzt.",
        "no_free_port": "Kein freier Port gefunden.",
        "free_port_found": "Freier Port gefunden: {port}.",
        # --- docker / errors ---
        "docker_unavailable": "Docker ist nicht verfuegbar (nicht gestartet).",
        "docker_not_installed": "Docker ist nicht installiert (docker nicht im PATH).",
        "docker_no_response": "Docker antwortet nicht.",
        "docker_running": "Docker laeuft.",
        "docker_stopped": "Docker Desktop ist nicht gestartet.",
        "compose_not_found": "Compose-Datei nicht gefunden: {path}",
        "build_failed": "Docker-Build fehlgeschlagen:\n{detail}",
        "build_timeout": "Docker-Build hat das Zeitlimit ueberschritten.",
        "start_failed": "Start fehlgeschlagen:\n{detail}",
        "start_timeout": "Start hat das Zeitlimit ueberschritten.",
        "not_reachable": "Installiert, aber {app} ist nicht erreichbar: {detail}",
        "container_not_running": "Container wurde gebaut, laeuft aber nicht.",
        "start_no_container": "Start-Befehl lief, aber kein Container laeuft.",
        "stop_failed": "Stoppen fehlgeschlagen: {detail}",
        "stop_not_verified": "Container laeuft nach dem Stop-Befehl noch.",
        "uninstall_partial": "Teilweise entfernt: {count} Container konnte(n) nicht entfernt werden.",
        "not_reachable_after": "{app} nicht erreichbar nach {timeout}s ({detail}).",
        "error": "Fehler: {msg}",
        "error_word": "Fehler",
    },
}


def t(key: str, config: LauncherConfig, **kwargs: Any) -> str:
    """Translate ``key`` for ``config.locale``; interpolate ``{app}`` + kwargs.

    Custom strings (``config.custom_strings``) take precedence over the
    built-in catalog. Missing keys fall back to English and finally to the
    key itself. A bad format placeholder is logged and the raw template
    returned rather than raising.
    """
    locale = config.locale
    template = (
        config.custom_strings.get(locale, {}).get(key)
        or STRINGS.get(locale, {}).get(key)
        or STRINGS[FALLBACK_LANG].get(key, key)
    )
    params: dict[str, Any] = {"app": config.app_name, **kwargs}
    try:
        return template.format(**params)
    except (KeyError, IndexError) as exc:
        logger.warning("i18n format failed for %r: %s", key, exc)
        return template


def available_languages() -> list[str]:
    """Sorted list of built-in language codes."""
    return sorted(STRINGS.keys())
