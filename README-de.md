# docker-app-launcher

Konfigurierbarer Desktop-Launcher für Docker-basierte Anwendungen.
**Ein dauerhaftes Fenster.** Es öffnet sich, zeigt den Fortschritt und
schließt sich nie von selbst — keine Dialog-Ketten.

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

- **Ein dauerhaftes Fenster** — schließt sich nie selbst; nur das X schließt es.
- **Docker-Prüfung beim Start** — unterscheidet *nicht installiert* / *läuft* / *gestoppt* / *kein Docker*.
- **Live-Build-Fortschritt** — der Docker-Build wird Zeile für Zeile ins Fenster gestreamt.
- **Konfigurierbarer Port** — im GUI und per `--port` editierbar, validiert und persistiert (in `launcher.json` und im `.env` neben der Compose-Datei, damit Launcher und Compose nicht auseinanderlaufen).
- **Live-Port-Wechsel** — das Port-Feld bleibt während des Betriebs editierbar; "Port anwenden" validiert, schreibt `.env` neu und erstellt den Stack in Sekunden neu (kein Rebuild — nur der veröffentlichte Host-Port änderte sich) und prüft dann die Gesundheit auf dem **neuen** Port.
- **Interne Ports (Experten)** — optionale `internal_ports` / `env_internal_port_keys` erlauben das Umbiegen von Container-internen Ports (voller Bereich 1–65535, z. B. nginx `:80`); ein einklappbarer "Erweiterte Einstellungen"-Bereich (per `show_advanced_ports`) wendet sie mit einem Image-Rebuild + Gesundheitsprüfung an. Standardmäßig leer: keine zusätzlichen `.env`-Schlüssel, keine UI, keine Verhaltensänderung.
- **Verifizierte Aktionen** — Installation prüft die Gesundheit; Deinstallation listet Container erneut auf.
- **Install-Manifest + Start-Aufräumen** — findet und entfernt auf Wunsch Reste alter Installationen.
- **Ausführliches Deinstallieren / Aufräumen** — jeder Schritt mit ✓ / ✗ Ergebnis.
- **Einzel-Instanz-Schutz** — eine PID-basierte Sperrdatei verweigert einen zweiten Start mit dem Hinweis "läuft bereits", statt ein zweites Fenster zu öffnen.
- **Update-Prüfung im Hintergrund** — prüft GitHub-Releases (abgeleitet aus `repo_url`) und meldet im Fenster, wenn eine neuere Version existiert. Abschaltbar via `update_check_enabled`; bei Netzwerkfehlern still.
- **Datei-Logging** — ein rotierendes `launcher.log` plus ein `install.log` pro Lauf im Config-Verzeichnis, sowie `launcher-debug.log` bei `--debug`. Beste-Bemühung: ein nicht beschreibbares Verzeichnis degradiert, statt abzustürzen.
- **Nebenläufigkeits-sichere Oberfläche** — während einer Aktion sind alle Buttons deaktiviert und das Fenster bleibt im Vordergrund, sodass keine zweite Aktion parallel startet.
- **Leise unter Windows** — jeder Docker-Subprozess läuft mit `CREATE_NO_WINDOW`, sodass eine Installation keinen Schwarm von Konsolenfenstern mehr aufblitzen lässt (unter Linux/macOS unverändert).
- **PyInstaller-fertig** — mitgelieferte Spec-Vorlage, Hidden-Imports-Liste und Versions-Injektion zur Build-Zeit für eingefrorene Einzeldatei-Builds.
- **System-Tray** (optional) — `pip install docker-app-launcher[tray]`.
- **DE/EN i18n (YAML)** — Strings liegen in Sprachdateien (`i18n/de.yaml`, `i18n/en.yaml`), die beim Start geladen werden; **eine neue Sprache fügt man durch Ablegen einer `<code>.yaml` hinzu**. Deutsch nutzt echte UTF-8-Umlaute. App-spezifische Überschreibungen via `custom_strings`.
- **Actions-Architektur** — getestet ohne GUI.
- **CLI ↔ GUI Parität** — beide rufen dieselbe Actions-Schicht auf.

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

## Verwendet von

- [Adaptive Learner](https://github.com/astrapi69/adaptive-learner) — KI-gestützte Sprachlernplattform
- [Bibliogon](https://github.com/astrapi69/bibliogon) — React-basierte Buch-Authoring-Plattform

## Lizenz

[MIT](LICENSE) © Asterios Raptis
