# docker-app-launcher

Konfigurierbarer Desktop-Launcher fuer Docker-basierte Anwendungen.
**Ein dauerhaftes Fenster.** Es oeffnet sich, zeigt den Fortschritt und
schliesst sich nie von selbst — keine Dialog-Ketten.

> 🇬🇧 [English version](README.md)

## Schnellstart

```bash
pip install docker-app-launcher
```

### Python-API (3 Zeilen)

```python
from docker_app_launcher import LauncherConfig, launch

launch(LauncherConfig(
    app_name="Meine App",
    container_name="meine-app",
    default_port=8080,
    locale="de",
))
```

### CLI

```bash
docker-app-launcher --config launcher.json   # Fenster oeffnen
docker-app-launcher --status                  # Status ausgeben und beenden
docker-app-launcher --install --port 9000     # bauen + starten (headless)
docker-app-launcher --check                   # laeuft Docker?
```

### launcher.json

Alles ist konfigurierbar. Nur `app_name` ist erforderlich — der Rest wird
abgeleitet (Slug, Container-/Image-Namen, Compose-Projekt, Config-Verzeichnis)
oder mit Standardwerten belegt.

```json
{
  "app_name": "Meine App",
  "container_name": "meine-app",
  "default_port": 8080,
  "compose_file": "docker-compose.prod.yml",
  "install_dir": "/opt/meine-app",
  "health_check_path": "/api/health",
  "repo_url": "https://github.com/owner/repo",
  "locale": "de"
}
```

## Funktionen

- **Ein dauerhaftes Fenster** — schliesst sich nie selbst; nur das X schliesst es.
- **Docker-Pruefung beim Start** — unterscheidet *nicht installiert* / *laeuft* / *gestoppt* / *kein Docker*.
- **Live-Build-Fortschritt** — der Docker-Build wird Zeile fuer Zeile ins Fenster gestreamt.
- **Konfigurierbarer Port** — im GUI und per `--port` editierbar, validiert und persistiert (in `launcher.json` und `.env`).
- **Verifizierte Aktionen** — Installation prueft die Gesundheit; Deinstallation listet Container erneut auf.
- **Install-Manifest + Start-Aufraeumen** — findet und entfernt auf Wunsch Reste alter Installationen.
- **Ausfuehrliches Deinstallieren / Aufraeumen** — jeder Schritt mit ✓ / ✗ Ergebnis.
- **System-Tray** (optional) — `pip install docker-app-launcher[tray]`.
- **DE/EN i18n** — mit App-spezifischen Ueberschreibungen via `custom_strings`.
- **Actions-Architektur** — getestet ohne GUI.
- **CLI ↔ GUI Paritaet** — beide rufen dieselbe Actions-Schicht auf.

## Lizenz

[MIT](LICENSE) © Asterios Raptis
