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
docker-app-launcher --version                 # Version ausgeben und beenden
docker-app-launcher --check                   # laeuft Docker?
docker-app-launcher --status                  # Status ausgeben und beenden
docker-app-launcher --install --port 9000     # bauen + starten (headless)
docker-app-launcher --start                   # gestoppte App starten
docker-app-launcher --stop                    # laufende App stoppen
docker-app-launcher --uninstall               # Container/Images entfernen
docker-app-launcher --cleanup                 # Reste entfernen
docker-app-launcher --open                    # App im Browser oeffnen
docker-app-launcher --debug ...               # ausfuehrliches Log auf stdout + launcher-debug.log
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
  "app_version": "0.2.2",
  "update_check_enabled": true,
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
- **Einzel-Instanz-Schutz** — eine PID-basierte Sperrdatei verweigert einen zweiten Start mit dem Hinweis "laeuft bereits", statt ein zweites Fenster zu oeffnen.
- **Update-Pruefung im Hintergrund** — prueft GitHub-Releases (abgeleitet aus `repo_url`) und meldet im Fenster, wenn eine neuere Version existiert. Abschaltbar via `update_check_enabled`; bei Netzwerkfehlern still.
- **Datei-Logging** — ein rotierendes `launcher.log` plus ein `install.log` pro Lauf im Config-Verzeichnis, sowie `launcher-debug.log` bei `--debug`. Beste-Bemuehung: ein nicht beschreibbares Verzeichnis degradiert, statt abzustuerzen.
- **Nebenlaeufigkeits-sichere Oberflaeche** — waehrend einer Aktion sind alle Buttons deaktiviert und das Fenster bleibt im Vordergrund, sodass keine zweite Aktion parallel startet.
- **Leise unter Windows** — jeder Docker-Subprozess laeuft mit `CREATE_NO_WINDOW`, sodass eine Installation keinen Schwarm von Konsolenfenstern mehr aufblitzen laesst (unter Linux/macOS unveraendert).
- **PyInstaller-fertig** — mitgelieferte Spec-Vorlage, Hidden-Imports-Liste und Versions-Injektion zur Build-Zeit fuer eingefrorene Einzeldatei-Builds.
- **System-Tray** (optional) — `pip install docker-app-launcher[tray]`.
- **DE/EN i18n** — mit App-spezifischen Ueberschreibungen via `custom_strings`.
- **Actions-Architektur** — getestet ohne GUI.
- **CLI ↔ GUI Paritaet** — beide rufen dieselbe Actions-Schicht auf.

## Lizenz

[MIT](LICENSE) © Asterios Raptis
