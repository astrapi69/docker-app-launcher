# docker-app-launcher

Konfigurierbarer Desktop-Launcher für Docker-basierte Anwendungen.
**Ein dauerhaftes Fenster.** Es öffnet sich, zeigt den Fortschritt und
schließt sich nie von selbst — keine Dialog-Ketten.

> 🇬🇧 [English version](README.md)

## Schnellstart

```bash
pip install docker-app-launcher            # tkinter-Fenster (keine Extra-Abhängigkeiten)
pip install "docker-app-launcher[ctk]"     # modernes CustomTkinter-Fenster
pip install "docker-app-launcher[qt]"      # PySide6-(Qt)-Fenster
pip install "docker-app-launcher[tray]"    # System-Tray-Unterstützung
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
docker-app-launcher --config launcher.json   # Fenster öffnen
docker-app-launcher --version                 # Version ausgeben und beenden
docker-app-launcher --check                   # läuft Docker?
docker-app-launcher --status                  # Status ausgeben und beenden
docker-app-launcher --install --port 9000     # bauen + starten (headless)
docker-app-launcher --start                   # gestoppte App starten
docker-app-launcher --stop                    # laufende App stoppen
docker-app-launcher --uninstall               # Container/Images entfernen
docker-app-launcher --cleanup                 # Reste entfernen
docker-app-launcher --open                    # App im Browser öffnen
docker-app-launcher --debug ...               # ausführliches Log auf stdout + launcher-debug.log
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
  "app_version": "0.4.0",
  "update_check_enabled": true,
  "internal_ports": { "nginx": 80 },
  "env_internal_port_keys": { "nginx": "NGINX_PORT" },
  "show_advanced_ports": true,
  "locale": "de"
}
```

> `internal_ports`, `env_internal_port_keys` und `show_advanced_ports` sind
> optionale Experten-Felder — ohne sie verhält sich der Launcher wie zuvor
> (ein Host-Port, kein Experten-Bereich).

## Funktionen

- Ein dauerhaftes Fenster (schließt sich nie selbst)
- Echtzeit-Fortschrittsbalken mit Parsing der Docker-Build-Schritte
- Docker-Prüfung beim Start
- Live-Build-Fortschritt (Zeile für Zeile gestreamt)
- Konfigurierbarer Port (GUI + CLI) mit Live-Validierung
- Interne Experten-Ports (einklappbar)
- 3 Zustände: nicht installiert / läuft / gestoppt
- Install-Manifest für präzises Aufräumen
- Aufräumen beim Start (aktive Volumes ausgeschlossen)
- System-Tray mit AppIndicator (Linux/Wayland) + Taskleisten-Fallback
- "Im Hintergrund weiterlaufen"-Button
- Eigene Fenster- + Tray-Icons
- Sprachauswahl mit OS-Autoerkennung (11 Sprachen)
- Single-Instance-Lockfile
- Persistentes Datei-Logging mit Rotation
- Ausführliche Deinstallation mit Schritt-für-Schritt-Verifizierung
- Update-Prüfung über die GitHub-Releases-API
- DE/EN + 9 weitere Sprachen (YAML-basiert, erweiterbar)
- Actions-Architektur (testbar ohne GUI)
- CLI ↔ GUI Parität

## Eigene Icons

Fenster- und System-Tray-Icons konfigurieren:

```python
launch(LauncherConfig(
    app_name="Meine App",
    icon_path="pfad/zum/app-icon.png",        # Fenster-Icon
    tray_icon_path="pfad/zum/tray-icon.png",   # Tray-Icon (optional, Fallback: icon_path)
))
```

Ohne Icon wird automatisch ein Standard-Icon mit dem Anfangsbuchstaben des App-Namens generiert.

Unterstützte Formate: PNG (empfohlen), ICO, BMP. Empfohlene Größe: 256x256 (Fenster), 64x64 (Tray).

## Aufräumen-Konfiguration

Konfiguriere welche Pfade nach alten Installationsresten durchsucht werden:

```python
launch(LauncherConfig(
    app_name="Meine App",
    container_name="meine-app",
    legacy_names=["alter-name", "prototyp-v1"],
    cleanup_configs=[
        "~/.alter-name",
        "~/.config/alter-name",
        "~/.local/share/alter-name",
    ],
    cleanup_search_paths=[
        "~/.config/",
        "~/.local/share/",
        "~/",
    ],
))
```

- `legacy_names`: Frühere Projektnamen für verwaiste Container/Images/Volumes.
- `cleanup_configs`: Explizite Konfigurationsverzeichnisse zum Aufräumen.
- `cleanup_search_paths`: Basisverzeichnisse die nach `legacy_names`-Unterverzeichnissen durchsucht werden (`<basis>/<name>` und `<basis>/.<name>`).
- Aktive Projekt-Volumes werden automatisch ausgeschlossen.
- Benutzer-Daten-Volumes sind standardmäßig nicht ausgewählt (Opt-in).

## Konfigurationspfade

Alle Launcher-Daten liegen unter `config_dir` (Standard: `~/.{app_slug}/`):

```
~/.meine-app/
  launcher.json          # Port, Einstellungen
  .env                   # Docker Compose Port-Variablen
  install-manifest.json  # Installierte Container, Images, History
  launcher.log           # Persistentes Log (rotiert, max 5 MB)
  install.log            # Letztes Install-/Rebuild-Log
  launcher.lock          # Single-Instance Lockfile
```

Konfigurationsverzeichnis ändern:

```python
launch(LauncherConfig(
    config_dir="~/.eigener-pfad/meine-app",
))
```

## Installations-Manifest

Der Launcher führt automatisch ein Installations-Manifest unter `{config_dir}/install-manifest.json`. Diese Datei protokolliert jedes Artefakt das bei der Installation erstellt wurde und ermöglicht präzises Aufräumen.

```json
{
  "installed_at": "2026-06-24T14:30:00Z",
  "updated_at": "2026-06-24T18:15:00Z",
  "app_name": "Meine App",
  "app_version": "1.95.0",
  "launcher_version": "0.5.0",
  "port": 8501,
  "compose_project": "meine-app",
  "containers": [
    {"name": "meine-app-frontend", "image": "meine-app-frontend:latest"},
    {"name": "meine-app-backend", "image": "meine-app-backend:latest"}
  ],
  "images": [
    "meine-app-frontend:latest",
    "meine-app-backend:latest"
  ],
  "volumes": [
    "meine-app-data"
  ],
  "install_history": [
    {"action": "install", "version": "1.94.0", "at": "2026-06-20T10:00:00Z"},
    {"action": "update", "version": "1.95.0", "at": "2026-06-24T14:30:00Z"}
  ]
}
```

Das Manifest wird:
- **Geschrieben** nach jeder erfolgreichen Installation oder Start (mit Rebuild).
- **Aktualisiert** mit Version und Zeitstempel bei jedem Start.
- **Erweitert** in `install_history` bei jeder Installation/Update/Deinstallation.
- **Als deinstalliert markiert** (nicht gelöscht) bei Deinstallation.

### Wie das Aufräumen das Manifest nutzt

Mit Manifest weiß das Aufräumen exakt welche Container, Images und Volumes zur aktuellen oder vorherigen Installation gehören. Ohne Manifest (alte Installationen) wird auf Namens-Muster zurückgegriffen.

```
Mit Manifest:    Präzise — entfernt nur gelistete Artefakte
Ohne Manifest:   Muster-basiert — sucht nach Namens-Mustern
```

Deshalb wird das Manifest automatisch erstellt und sollte nicht manuell gelöscht werden.

## Fortschrittsbalken

Der Launcher zeigt einen Echtzeit-Fortschrittsbalken während Installation, Start, Aufräumen und Deinstallation.

Bei Docker-Builds wird der Fortschritt aus der Build-Ausgabe geparst (Schritt N/M). Konfiguriere eine Schätzung für den ersten Build:

```json
{
  "estimated_build_steps": 38
}
```

0 (Standard) bedeutet Auto-Erkennung aus der Docker-Ausgabe.

## Sprachauswahl

Der Launcher erkennt automatisch die Systemsprache. Ein Dropdown erlaubt jederzeit den Wechsel. Unterstützt: Deutsch, English, Ελληνικά, Español, Français, हिन्दी, 日本語, 한국어, Português, Türkçe, Bahasa Indonesia.

```json
{
  "locale": "auto"
}
```

`"auto"` erkennt die OS-Sprache. Setze einen festen Code (`"de"`, `"en"`, `"ja"`, ...) zum Überschreiben.

## Single-Instance

Verhindert das gleichzeitige Starten mehrerer Instanzen.

```json
{
  "single_instance": true
}
```

## Logging

Der Launcher schreibt persistente Logs zur Diagnose:

```
~/.meine-app/
  launcher.log    # Persistent, rotiert (Standard 5 MB, 3 Backups)
  install.log     # Pro Installation/Rebuild überschrieben
```

Mit `--debug`: zusätzlich ein `launcher-debug.log` im aktuellen Verzeichnis.

```json
{
  "log_level": "INFO",
  "log_max_size": 5000000,
  "log_backup_count": 3
}
```

## Aufräum-Sicherheit

Das Aufräumen beim Start schließt aktive Projekt-Volumes automatisch aus. Nur veraltete Artefakte früherer oder alter Installationen werden zum Entfernen angeboten.

Übersprungene Einträge werden explizit protokolliert:

```
Volume 'meine-app-data' übersprungen (aktives Projekt)
Volume 'alte-app-data' wird entfernt... ✓
```

## Docker-Prüfung

Der Launcher prüft die Docker-Verfügbarkeit beim Start mit plattformspezifischer Diagnose und bietet die passende nächste Aktion an (Daemon/Desktop starten oder die Installationsanleitung öffnen):

| Plattform | Prüfungen | Start-Aktion |
|-----------|-----------|--------------|
| Linux | docker-Binary + systemd-Daemon + Gruppenzugehörigkeit | `systemctl start docker` (über `pkexec`) |
| Windows | docker-Binary + Docker-Desktop-Pfad + Daemon | Startet `Docker Desktop.exe` |
| macOS | docker-Binary + Docker.app + Daemon | `open /Applications/Docker.app` |

Den Docker-Desktop-Pfad oder die Installations-URL überschreiben:

```json
{
  "docker_desktop_path": "/eigener/pfad/Docker Desktop.exe",
  "docker_install_url": "https://meine-firma.de/docker-setup"
}
```

## GUI-Frontends

Das Fenster wird von einem austauschbaren Frontend gerendert, ausgewählt über
das Konfigurationsfeld `gui_backend`. Alle Frontends teilen sich dieselben
Verhaltens-Tabellen (`ui_model.py`) — Button-Layout, Aktivierung pro Zustand,
Tooltips und Schließ-Verhalten sind konstruktionsbedingt identisch, nur das
Widget-Toolkit unterscheidet sich.

| `gui_backend` | Toolkit | Installation |
|---------------|---------|--------------|
| `"tk"` (Standard) | tkinter (Standardbibliothek) | nichts weiter nötig |
| `"ctk"` | CustomTkinter — moderne Optik, folgt Hell/Dunkel des Systems | `pip install "docker-app-launcher[ctk]"` |
| `"qt"` | PySide6 (Qt) — native Widgets | `pip install "docker-app-launcher[qt]"` |

```json
{ "app_name": "Meine App", "gui_backend": "ctk" }
```

Drittanbieter-Pakete können weitere Frontends über die Entry-Point-Gruppe
`docker_app_launcher.frontends` registrieren; jedes Modul mit
`run(config, *, debug=False) -> int` genügt dem Vertrag.

## Entwicklung

```bash
poetry install --with dev --all-extras
make ci           # Lint + Format-Check + Typecheck + Tests
make test         # Tests mit Coverage
make test-gui     # Fenster-Tests (braucht Display oder xvfb-run)
make screenshots  # Dark-Mode-Screenshots aller drei Frontends -> test-screenshots/
make fix          # Lint + Format automatisch korrigieren
```

### Manuelles Testen des Launchers

Beispiel-Konfigurationen unter `test-configs/` lassen den Launcher gegen eine
echte App-Konfiguration laufen, ohne selbst eine schreiben zu müssen. Die
`launcher-*`-Targets lesen `TEST_CONFIG` (Standard
`test-configs/adaptive-learner.json`):

```bash
make launcher-test               # GUI im Debug-Modus öffnen
make launcher-status             # Zustand der App ausgeben und beenden
make launcher-check              # Docker-Verfügbarkeit prüfen und beenden
make launcher-stop               # App stoppen
make launcher-cleanup            # Installationsreste entfernen
make launcher-version            # Launcher-Version ausgeben

# eine mitgelieferte Konfiguration explizit wählen
make launcher-test-al            # test-configs/adaptive-learner.json
make launcher-test-bibliogon     # test-configs/bibliogon.json
make launcher-test-minimal       # test-configs/minimal.json

# oder auf eine beliebige Konfiguration zeigen
make launcher-test TEST_CONFIG=pfad/zu/deiner.json

make smoke                       # Version + jede Test-Konfig parst + --check
```

## Verwendet von

- [Adaptive Learner](https://github.com/astrapi69/adaptive-learner) — KI-gestützte Sprachlernplattform
- [Bibliogon](https://github.com/astrapi69/bibliogon) — React-basierte Buch-Authoring-Plattform

## Lizenz

[MIT](LICENSE) © Asterios Raptis
