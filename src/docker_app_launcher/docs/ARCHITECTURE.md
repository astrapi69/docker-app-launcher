# docker-app-launcher — Architektur-Dokument

Version: 0.14.0
Stand: 23.07.2026

---

## Überblick

docker-app-launcher ist ein konfigurierbarer Desktop-Launcher
für Docker-basierte Anwendungen. Ein persistentes Fenster
das Container verwaltet, Build-Fortschritt streamt und sich
nie von selbst schließt.

Kernprinzip: **3 Zeilen pro App.**

```python
from docker_app_launcher import LauncherConfig, launch

launch(LauncherConfig(
    app_name="My App",
    container_name="my-app",
    default_port=8080,
))
```

---

## Schichtenarchitektur

```
┌─────────────────────────────────────────┐
│              Einstiegspunkte            │
│  __init__.py (launch API)               │
│  __main__.py (CLI + GUI Router)         │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│         Frontends (dünn, tauschbar)    │
│  frontends/: Registry (gui_backend)    │
│   - gui.py  = "tk"  (tkinter, Default) │
│   - ctk.py  = "ctk" (CustomTkinter)    │
│   - qt.py   = "qt"  (PySide6)          │
│  ui_model.py: geteilte Verhaltens-     │
│   Tabellen (Buttons, Zustände,        │
│   Dispatch, Close-Policy)              │
│  - Zeigt Ergebnisse, ruft actions auf │
│  - KEINE Business-Logik                │
│  - Ein Fenster, schließt sich nie     │
└──────────────┬──────────────────────────┘
               │ ruft auf
┌──────────────▼──────────────────────────┐
│              Actions (pure)             │
│  actions.py                            │
│  - ALLE Business-Logik                 │
│  - Kein Tk Import                      │
│  - Jede Funktion: (bool, str) raus     │
│  - Jede Funktion: verifiziert Ergebnis │
│  - 300+ Tests ohne GUI                 │
└──────────────┬──────────────────────────┘
               │ nutzt
┌──────────────▼──────────────────────────┐
│              Infrastruktur              │
│  config.py    - LauncherConfig          │
│  i18n/        - YAML Sprachdateien     │
│  tray.py      - System-Tray (optional) │
│  lockfile.py  - Single-Instance        │
│  logging_setup.py - File Logging       │
└─────────────────────────────────────────┘
```

### Schicht 1: Einstiegspunkte

**`__init__.py`** — Public API
```python
from docker_app_launcher import LauncherConfig, launch
```
Zwei Exporte. Mehr braucht kein Consumer.

**`__main__.py`** — CLI + GUI Router
```python
# CLI-Modus: Kommando ausführen, beenden
docker-app-launcher --status
docker-app-launcher --install --port 9000

# GUI-Modus: Fenster öffnen (kein CLI-Flag)
docker-app-launcher
docker-app-launcher --config launcher.json --debug
```

Entscheidungslogik:
```
CLI-Flag vorhanden?
├─ Ja → Action ausführen → print → exit
└─ Nein → LauncherApp(config) → mainloop
```

### Schicht 2: GUI (gui.py)

**LauncherApp** erbt von `tk.Tk`.
EIN Fenster. Öffnet sich. Schließt sich NIE von selbst.

Aufgaben:
- Zustand anzeigen (Text + Buttons aktualisieren)
- User-Input entgegennehmen (Klicks, Port-Eingabe)
- Actions in Threads ausführen (blocking → non-blocking)
- Ergebnisse über `self.after()` in den Tk-Thread marshallen

NICHT Aufgaben:
- Docker-Befehle ausführen
- Port validieren
- Health-Checks machen
- Dateien lesen/schreiben

```python
class LauncherApp(tk.Tk):
    def _on_action(self, action_name, action_fn, *args):
        self._disable_buttons()
        threading.Thread(target=worker, daemon=True).start()
        # Ergebnis über self.after() → _on_action_result()
```

### Schicht 3: Actions (actions.py)

Pure Python. Kein Tk. Jede Funktion testbar.

```python
def check_docker() -> tuple[bool, str]
def get_state(config) -> str  # 'no_docker'|'not_installed'|'running'|'stopped'
def install(config, on_step, on_output, on_progress) -> tuple[bool, str]
def start(config, on_step, on_output) -> tuple[bool, str]
def stop(config) -> tuple[bool, str]
def uninstall(config, on_step) -> tuple[bool, str]
def health_check(config) -> tuple[bool, str]
def check_port(port) -> tuple[bool, str]
def change_port(config, new_port) -> tuple[bool, str]
def change_internal_port(config, port_key, new_port) -> tuple[bool, str]
def open_browser(config) -> None
def find_stale_artifacts(config) -> dict
def cleanup_stale(config, selected, on_step) -> tuple[bool, str]
def write_manifest(config, version) -> None
def read_manifest(config) -> dict | None
def ensure_installed(config, ...) -> tuple[bool, str]
```

Callback-Pattern für Streaming:
```python
def install(config, on_step=None, on_output=None, on_progress=None):
    if on_step: on_step("Docker prüfen...")
    ok, msg = check_docker()
    if on_step: on_step("Image bauen...")
    for line in _stream_command(cmd):
        if on_output: on_output(line)
        if on_progress: on_progress(percent, label)
```

GUI übergibt Callbacks die in den Tk-Thread marshallen:
```python
# GUI:
actions.install(
    config,
    on_step=lambda s: self.after(0, lambda: self._log(s)),
    on_output=lambda l: self.after(0, lambda: self._log_line(l)),
    on_progress=lambda p, l: self.after(0, lambda: self._update_progress(p, l)),
)
```

### Schicht 4: Infrastruktur

**config.py** — LauncherConfig Dataclass
Einzige Wahrheitsquelle für ALLE Konfiguration.
Nichts im Code ist hardcoded.

**i18n/** — YAML Sprachdateien (11 Sprachen)
Flache Keys. `t("install", config)` → "Installieren".
Custom Strings aus Config haben Vorrang.

**tray.py** — System-Tray (optional)
AppIndicator-Backend auf Linux/Wayland.
Fallback auf Taskbar-Minimize.
pystray + Pillow + PyGObject als optionale Dependencies.

**lockfile.py** — Single-Instance
Verhindert Doppelstart. Cross-Platform (fcntl/msvcrt).

**logging_setup.py** — File Logging
launcher.log (persistent, rotiert), install.log (pro Install),
launcher-debug.log (bei --debug).

---

## GUI-Frontends & ui_model (ab 0.14.0)

Das Fenster ist eine austauschbare Schicht. Jede ENTSCHEIDUNG des Fensters
liegt in `ui_model.py` — framework-frei:

- `BUTTON_STATES`: Zustand → welcher Button aktiv (die eine Wahrheit)
- `PRIMARY_BUTTONS` / `PRIMARY_GRID` / `SECONDARY_BUTTONS`: Layout-Tabellen
- `disabled_reason_key()`: Tooltip-Begründung pro deaktiviertem Button
- `dispatch_action()`: action_id → actions-Aufruf
- `should_keep_alive_on_close()` / `should_minimize_to_tray()`: Close-Policy

Ein Frontend ist ein Modul mit `run(config, *, debug=False) -> int`. Auswahl
über das Config-Feld `gui_backend`; Auflösung in `frontends/__init__.py`:

1. Eingebaut: `"tk"` (gui.py), `"ctk"` (frontends/ctk.py, Extra `ctk`),
   `"qt"` (frontends/qt.py, Extra `qt`)
2. Drittanbieter: Entry-Point-Gruppe `docker_app_launcher.frontends`

Da alle Frontends dieselben `ui_model`-Tabellen rendern, kann Verhalten
zwischen Toolkits nicht divergieren — die Fenster-Testsuiten aller drei
Frontends prüfen identische Erwartungen gegen dieselben Tabellen.
Toolkit-Eigenheiten bleiben im jeweiligen Frontend: Tk marshalt
Worker-Threads per `after()`, Qt per queued Signal; der pystray-Hintergrund
läuft in Qt über einen kleinen `withdraw`/`iconify`-Adapter.

---

## Fenster-Layout

```
┌─────────────────────────────────────┐
│  {App Name} {Status} auf Port {X}   │
│  Port: [8501] ✓                     │
│  Sprache: [dropdown]                │
│  ▶ Erweiterte Einstellungen         │
│                                     │
│  [Hauptaktion 1] [Hauptaktion 2]    │
│  [Hauptaktion 3] [Log kopieren]     │
│                                     │
│  ┌─── Log-Bereich (scrollbar) ───┐  │
│  │ Docker prüfen... ✓           │  │
│  │ Image bauen...                │  │
│  │ #5 [frontend 2/6] COPY ...   │  │
│  └───────────────────────────────┘  │
│  [████████████░░░░░░░] 60%          │
│                                     │
│  ────────── Trennlinie ──────────── │
│  [Aufräumen]  [Im Hintergrund]      │
└─────────────────────────────────────┘
```

---

## Zustandsmaschine

```
                    ┌───────────┐
                    │ no_docker │
                    └─────┬─────┘
                          │ Docker starten
                          ▼
                  ┌───────────────┐
         ┌───────│ not_installed  │◄──────┐
         │       └───────┬───────┘       │
         │               │ install       │ uninstall
         │               ▼               │
         │       ┌───────────────┐       │
         │  ┌───►│   running     │───┐   │
         │  │    └───────┬───────┘   │   │
         │  │            │ stop      │   │
         │  │            ▼           │   │
         │  │    ┌───────────────┐   │   │
         │  └────│   stopped     │───┘   │
         │ start └───────┬───────┘       │
         │               │ uninstall     │
         │               └──────────────►│
         └───────────────────────────────┘
```

### Button-States pro Zustand

| Button | no_docker | not_installed | stopped | running |
|--------|-----------|---------------|---------|---------|
| Installieren | disabled | **enabled** | disabled | disabled |
| Starten | disabled | disabled | **enabled** | disabled |
| Stoppen | disabled | disabled | disabled | **enabled** |
| Deinstallieren | disabled | disabled | **enabled** | **enabled** |
| Im Browser öffnen | disabled | disabled | disabled | **enabled** |
| Log kopieren | disabled | **enabled** | **enabled** | **enabled** |
| Port übernehmen | disabled | disabled | **enabled** | **enabled** |
| Aufräumen | disabled | **enabled** | **enabled** | **enabled** |
| Im Hintergrund | disabled | disabled | disabled | **enabled** |

Buttons werden NIE versteckt. Nur enabled/disabled.
Disabled Buttons sind grau.

---

## Konfiguration (LauncherConfig)

Alles konfigurierbar. Nichts hardcoded.

### Kategorien

```python
@dataclass
class LauncherConfig:
    # App-Identität
    app_name: str
    container_name: str
    app_slug: str              # auto: kebab-case von app_name
    image_name: str            # auto: container_name
    compose_project: str       # auto: container_name

    # Netzwerk
    default_port: int = 8080
    health_check_path: str = "/api/health"
    health_check_key: str = "status"
    health_check_value: str = "ok"
    health_check_timeout: int = 60
    browser_path: str = "/"
    env_port_key: str = "APP_PORT"

    # Docker
    compose_file: str = "docker-compose.prod.yml"
    build_timeout: int = 600
    start_timeout: int = 120
    stop_timeout: int = 30
    estimated_build_steps: int = 0  # 0 = auto-detect

    # Pfade
    icon_path: str = ""
    tray_icon_path: str = ""   # Fallback: icon_path
    config_dir: str = ""       # auto: ~/.{app_slug}
    install_dir: str = ""
    manifest_file: str = "install-manifest.json"

    # GUI
    window_width: int = 620
    window_height: int = 520
    locale: str = "auto"       # auto = OS-Erkennung

    # Links
    repo_url: str = ""
    releases_url: str = ""     # auto: {repo_url}/releases/latest
    docs_url: str = ""

    # Cleanup
    cleanup_on_start: bool = True
    legacy_names: list[str]
    cleanup_configs: list[str]
    cleanup_search_paths: list[str]

    # Features
    single_instance: bool = True
    tray_enabled: bool = True
    tray_minimize_on_close: bool = True
    update_check_enabled: bool = True
    show_advanced_ports: bool = False

    # Interne Ports
    internal_ports: dict[str, int]
    env_internal_port_keys: dict[str, str]

    # Logging
    log_level: str = "INFO"
    log_max_size: int = 5_000_000
    log_backup_count: int = 3

    # Docker
    docker_desktop_path: str = ""
    docker_install_url: str = ""

    # i18n Override
    custom_strings: dict

    # Callbacks
    on_before_install: Callable | None
    on_after_install: Callable | None
    on_before_start: Callable | None
    on_after_start: Callable | None
    on_error: Callable | None
```

### Laden

```python
# Aus JSON
config = LauncherConfig.from_json("launcher.json")

# Programmatisch
config = LauncherConfig(app_name="My App", ...)

# Auto-Resolve
config.resolve()  # Füllt Defaults aus app_name
```

### Priorität

1. Expliziter Wert im Code (höchste)
2. launcher.json
3. CLI-Flag (--port, --debug)
4. Computed Default (resolve())

---

## Port-Management

### Öffentlicher Port (kein Rebuild)

```
User ändert Port 8501 → 9000:
1. Validieren (1024-65535, nicht belegt)
2. launcher.json aktualisieren
3. .env aktualisieren (APP_PORT=9000)
4. Stop Container
5. Start Container (compose up -d, KEIN --build)
6. Health-Check auf Port 9000
7. Browser öffnet Port 9000
Dauer: ~5 Sekunden
```

### Interner Port (Rebuild nötig)

```
User ändert Backend-Port 8000 → 8001:
1. Warnung: "Rebuild nötig, 2-5 Minuten"
2. .env aktualisieren (APP_BACKEND_PORT=8001)
3. Stop Container
4. Build + Start (compose up --build -d)
5. Health-Check
Dauer: 2-5 Minuten
```

### .env Synchronisation

```env
APP_PORT=8501
APP_BACKEND_PORT=8000
APP_NGINX_PORT=80
```

docker-compose.yml liest alle Ports aus .env.
Launcher schreibt .env VOR dem Start.
Kein Port-Mismatch möglich.

---

## Cleanup-System

### Install-Manifest

```json
{
  "installed_at": "2026-06-24T14:30:00Z",
  "app_version": "1.95.0",
  "containers": [...],
  "images": [...],
  "volumes": [...],
  "install_history": [...]
}
```

Geschrieben nach jedem Install/Start.
Ermöglicht präzises Cleanup ohne Raten.

### Cleanup-Flow

```
1. find_stale_artifacts(config)
   ├─ Manifest vorhanden? → präzise Liste
   └─ Kein Manifest? → Pattern-basierte Suche
2. Aktive Projekt-Volumes AUSFILTERN
3. User wählt (Daten-Volumes default AUS)
4. cleanup_stale(config, selected, on_step)
   └─ Jeden Schritt einzeln loggen mit ✓/✗
5. Summary: "X Artefakte, Y MB freigegeben"
```

### Suchquellen

| Quelle | Methode |
|--------|---------|
| Container | docker ps -a --filter name={name} |
| Images | docker images --filter reference={name} |
| Volumes | docker volume ls --filter name={name} |
| Config-Dirs | cleanup_configs + cleanup_search_paths |

---

## System-Tray

### Backend-Auswahl

```python
try:
    # Linux/Wayland: AppIndicator forcen
    from pystray._appindicator import Icon as _TrayIcon
except ImportError:
    try:
        # Fallback: pystray Auto-Detect (Windows/macOS)
        import pystray
        _TrayIcon = pystray.Icon
    except ImportError:
        _TrayIcon = None
```

### Fallback-Kette

```
Tray verfügbar + App läuft?
├─ Ja → withdraw() → Tray-Icon
│       Doppelklick → Fenster zurück
│       Menu: Öffnen / Browser / Stoppen / Beenden
└─ Nein → iconify() → Taskbar
          Klick auf Taskbar → Fenster zurück
```

### X-Button Verhalten

| App-Status | Tray verfügbar | X-Button Aktion |
|------------|-----------------|-----------------|
| Läuft | Ja | → Tray |
| Läuft | Nein | → Taskbar + Hinweis |
| Nicht läuft | Egal | → Schließen |

---

## i18n

### Struktur

```
i18n/
  de.yaml    # Deutsch
  en.yaml    # Englisch (Fallback)
  el.yaml    # Griechisch
  es.yaml    # Spanisch
  fr.yaml    # Französisch
  hi.yaml    # Hindi
  ja.yaml    # Japanisch
  ko.yaml    # Koreanisch
  pt.yaml    # Portugiesisch
  tr.yaml    # Türkisch
  id.yaml    # Indonesisch
```

### Flache Keys

```yaml
# de.yaml
running: "{app} läuft auf Port {port}."
install: "Installieren"
cleanup_done: "Aufräumen abgeschlossen. {count} Artefakte entfernt."
```

### Auflösung

```
1. Custom Strings (config.custom_strings) → höchste Prio
2. YAML Datei für config.locale
3. en.yaml als Fallback
4. Key selbst als letzter Fallback
```

### Locale Auto-Detection

```python
locale.getdefaultlocale() → "de_DE" → "de"
```

Dropdown im GUI für manuellen Wechsel.
Sprachen in nativer Schrift: "Deutsch", "Ελληνικά", "日本語".

---

## Docker-Check (Plattform-spezifisch)

| Plattform | Prüfung | Start-Aktion |
|-----------|----------|-------------|
| Linux | docker binary + systemd daemon + docker-Gruppe | systemctl start docker (pkexec) |
| Windows | docker binary + Docker Desktop Pfad + daemon | Docker Desktop.exe starten |
| macOS | docker binary + Docker.app + daemon | open /Applications/Docker.app |

Spezifische Fehlermeldungen:
- "Docker nicht installiert" → Install-Link
- "Docker nicht gestartet" → Start-Button
- "Keine Berechtigung" → usermod Anleitung (Linux)

Context-Fallback (ab 0.13.0): Ist der AKTIVE docker context nicht
erreichbar (z. B. Docker Desktop für Linux mit totem Socket), probiert die
Erkennung die übrigen Contexte per DOCKER_HOST-Probe durch und verbindet
über den funktionierenden Endpoint — als modulweiter DOCKER_HOST-Override
für alle folgenden docker-Kommandos (`actions.docker_host_override()`).
Bei Totalausfall nennt die Meldung Context, Endpoint und dockers erste
stderr-Zeile statt eines nackten "Docker ist nicht gestartet."

---

## Progressbar

### Zwei Modi

**Determinate** (Schritte bekannt):
```
[████████████░░░░░░░░] 60%  Image bauen...
```

**Indeterminate** (Dauer unbekannt):
```
[═══════════════════►]  Bereitschaft prüfen...
```

### Docker Build Parser

```python
class DockerBuildProgress:
    def parse_line(self, line: str):
        # "#22 [frontend build 4/6] RUN npm ci"
        # → step_num=22, current=4, total=6
        match = re.search(r'#(\d+)\s+\[.*?(\d+)/(\d+)\]', line)
```

Fallback: estimated_build_steps aus Config.

---

## CLI ↔ GUI Paritaet

| Action | CLI | GUI |
|--------|-----|-----|
| Docker prüfen | --check | Automatisch |
| Status | --status | Automatisch |
| Installieren | --install | [Installieren] |
| Starten | --start | [Starten] |
| Stoppen | --stop | [Stoppen] |
| Deinstallieren | --uninstall | [Deinstallieren] |
| Browser öffnen | --open | [Im Browser öffnen] |
| Port setzen | --port 9000 | Port-Feld |
| Aufräumen | --cleanup | [Aufräumen] |
| Version | --version | Im Titel |
| Debug | --debug | Log-Dateien |

---

## Dateistruktur

```
src/docker_app_launcher/
  __init__.py           # Public API
  __main__.py           # CLI + GUI Router
  config.py             # LauncherConfig
  actions.py            # Alle Business-Logik
  ui_model.py           # Framework-freie UI-Verhaltens-Tabellen
  gui.py                # LauncherApp(tk.Tk) — Frontend "tk"
  frontends/
    __init__.py          # Registry: gui_backend -> Frontend-Modul
    ctk.py               # CustomTkinter-Frontend (Extra "ctk")
    qt.py                # PySide6-Frontend (Extra "qt")
  tray.py               # System-Tray (optional)
  lockfile.py           # Single-Instance
  logging_setup.py      # File Logging
  i18n/
    __init__.py          # Loader + t()
    de.yaml
    en.yaml
    el.yaml es.yaml fr.yaml hi.yaml
    ja.yaml ko.yaml pt.yaml tr.yaml id.yaml
  packaging/
    __init__.py
    build_info.py        # Version Template
    launcher.spec.j2     # PyInstaller Template
    build.py             # Build CLI
tests/
  conftest.py
  test_actions.py        # 250+ Tests
  test_config.py
  test_cli.py
  test_gui_helpers.py
  test_i18n.py
  test_integration.py
test-configs/
  adaptive-learner.json
  bibliogon.json
  minimal.json
```

---

## Deployment

### PyPI

```bash
pip install docker-app-launcher
pip install docker-app-launcher[tray]  # + System-Tray
```

### Frozen Binary (PyInstaller)

```bash
pip install docker-app-launcher[build]
docker-app-launcher-build --config launcher.json --output dist/
```

### Consumers

| App | Config | Beschreibung |
|-----|--------|-------------|
| Adaptive Learner | launcher.json | KI-gestützte Sprachlern-App |
| Bibliogon | launcher.json | React-basierte Buch-Authoring-Plattform |

---

## Tests

| Kategorie | Anzahl | Framework |
|-----------|--------|-----------|
| Actions | 160+ | pytest |
| Config / CLI / i18n / Infra | 150+ | pytest |
| Fenster-Tests Tk (echtes Fenster) | 47 | pytest (+ Display/xvfb) |
| Fenster-Tests CustomTkinter | 17 | pytest (+ Display/xvfb) |
| Fenster-Tests Qt | 20 | pytest (offscreen, ohne Display) |
| Frontend-Registry | 10 | pytest |
| **Gesamt** | **490+** | **pytest** |

Docker gemockt, kein Shell-Out. Die Fenster-Suiten fahren echte Fenster
über die Event-Schicht des Toolkits (invoke()/click(), Threads synchron
gefakt) und skippen sauber ohne Display; die Qt-Suite läuft immer
(offscreen-Plattform). Mit DAL_SCREENSHOTS=1 entstehen Dark-Mode-
Screenshots aller Zustände und Sprachen (make screenshots; in CI als
Artefakt "gui-screenshots").

---

## Release-Prozess

1. Code-Änderung → PR gegen main
2. CI gruen (Tests + Lint + Typecheck)
3. Version bump in pyproject.toml
4. CHANGELOG.md aktualisieren
5. Tag pushen → CI published automatisch zu PyPI
6. Consumer (AL, Bibliogon) Version bumpen

Kein Release für Docs-only Änderungen.

---

## Design-Entscheidungen

| Entscheidung | Begründung |
|-------------|-------------|
| Ein Fenster, nie schließen | 15+ PRs und ein halber Tag Debugging mit Dialog-Ketten |
| Actions pure, GUI dünn | Testbarkeit ohne Display/Docker |
| Callbacks statt Returns | Streaming, Fortschrittsanzeige |
| YAML i18n statt Python dict | Skalierbar auf neue Sprachen ohne Code |
| AppIndicator forcen | Xorg-Backend funktioniert nicht auf Wayland |
| Flache Keys | Kein Umbau der Call-Sites |
| Buttons disablen statt verstecken | User sieht immer alle Möglichkeiten |
| .env neben compose-file | Docker liest .env relativ zum Compose-Pfad |
| Manifest für Cleanup | Präzises Aufraumen ohne Raten |
| estimated_build_steps | Progressbar auch ohne Parser-Treffer |
