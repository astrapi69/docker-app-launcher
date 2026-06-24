"""Optional system-tray support for the persistent launcher window.

``pystray`` + ``Pillow`` are an OPTIONAL dependency (the ``tray`` extra:
``pip install docker-app-launcher[tray]``). When they are NOT installed the
launcher behaves exactly as before - the window's X closes it - and nothing
here crashes. When they ARE installed AND the app is running, the window
minimizes to the system tray instead, exposing a right-click menu and
click-to-restore.

This module owns ONLY the tray-icon lifecycle. Every menu action routes back
through the callbacks the caller supplies; no business logic lives here. It is
import-safe without the extra, so the rest of the launcher - and its tests -
never depend on ``pystray`` being present.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docker_app_launcher import i18n
from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.tray")

_IMPORT_ERROR: str | None = None
_TRAY_BACKEND = "none"

try:
    import pystray
    from PIL import Image

    # Force the AppIndicator backend. pystray's auto-selection picks the legacy
    # X11/Xorg backend on Ubuntu/Wayland, which fires its setup callback but then
    # silently fails to dock - hiding the window then would strand the user.
    # AppIndicator (via PyGObject + the AppIndicator typelib, the 'tray' extra)
    # is the reliable path. Fall back to pystray's auto-selected Icon only when
    # AppIndicator is unavailable; start() still refuses the unreliable backend.
    try:
        from pystray._appindicator import Icon as _TrayIcon

        _TRAY_BACKEND = "appindicator"
    except Exception:  # noqa: BLE001 - gi / AppIndicator typelib may be missing
        _TrayIcon = pystray.Icon
        _TRAY_BACKEND = getattr(_TrayIcon, "__module__", "auto")
    HAS_TRAY = True
except Exception as exc:  # noqa: BLE001 - importing pystray can fail beyond ImportError
    # On Linux, importing pystray eagerly selects a backend, which can raise
    # non-ImportError errors when no usable tray is present (headless box, GTK
    # init failure). Any such failure must DISABLE the tray, never crash the
    # launcher on startup.
    pystray = None
    Image = None  # type: ignore[assignment, unused-ignore]
    _TrayIcon = None  # type: ignore[assignment, unused-ignore]
    HAS_TRAY = False
    _IMPORT_ERROR = repr(exc)

# pystray backends that do NOT reliably dock on modern Linux desktops. The
# legacy X11 XEmbed backend fires its setup callback but then silently fails to
# dock on GNOME/Wayland - hiding the window then would leave no way back, so we
# refuse it and fall back to a plain close. AppIndicator is the reliable path.
_UNRELIABLE_BACKENDS = ("pystray._xorg",)

# The tray menu, in display order: (action_id, i18n_label_key). action_id maps
# 1:1 to a caller-supplied callback. Pure data so it is unit-testable.
MENU_SPEC: tuple[tuple[str, str], ...] = (
    ("open", "tray_open"),
    ("open_browser", "open_browser"),
    ("stop", "stop"),
    ("quit", "tray_quit"),
)


def tray_available() -> bool:
    """True when ``pystray`` + ``Pillow`` are importable (the ``tray`` extra)."""
    return HAS_TRAY


def try_minimize_to_background(root: Any, tray_controller: Any) -> str:
    """Minimize the launcher to the background, preferring the system tray.

    Returns the mode used so the caller can give the right feedback:

    - ``"tray"`` - ``tray_controller.start()`` docked an icon; the window is
      withdrawn (it lives in the tray, restored from the tray menu).
    - ``"iconify"`` - the tray is missing/unreliable/failed (e.g. no
      AppIndicator on Ubuntu); the window is minimized to the TASKBAR instead,
      so the user can always click it back. No tray icon in this case.

    ``root`` is the Tk window (duck-typed: ``withdraw`` / ``iconify``), so this
    stays tkinter-free and unit-testable. ``tray_controller`` may be ``None``
    (then it always iconifies).
    """
    if tray_controller is not None and tray_controller.start():
        root.withdraw()
        return "tray"
    root.iconify()
    return "iconify"


def log_diagnostics(config: LauncherConfig) -> None:
    """Log tray-availability breadcrumbs, one per step (visible under ``--debug``).

    Answers "why is there no tray icon?" without the user debugging: whether the
    extra imported, which backend pystray selected (AppIndicator is the reliable
    one; the legacy X11 backend is refused), and whether the icon file resolves.
    """
    if HAS_TRAY:
        logger.debug("Tray: pystray + Pillow import: ok")
        if _TRAY_BACKEND == "appindicator":
            logger.debug("Tray: AppIndicator backend forced: ok (reliable)")
        elif _TRAY_BACKEND in _UNRELIABLE_BACKENDS:
            logger.debug("Tray: AppIndicator unavailable; fell back to %s (unreliable) -> taskbar", _TRAY_BACKEND)
        else:
            logger.debug("Tray: AppIndicator unavailable; fell back to %s", _TRAY_BACKEND)
    else:
        logger.debug("Tray: pystray + Pillow import: FAILED (%s) -> will fall back to taskbar", _IMPORT_ERROR)
    if not config.icon_path:
        logger.debug("Tray: icon: none configured")
    else:
        found = Path(config.icon_path).expanduser().is_file()
        logger.debug("Tray: icon %s: %s", config.icon_path, "found" if found else "MISSING")


def menu_action_ids() -> list[str]:
    """Return the tray menu action ids in display order. Pure (no pystray)."""
    return [action_id for action_id, _ in MENU_SPEC]


def menu_labels(config: LauncherConfig) -> dict[str, str]:
    """Localized tray-menu labels keyed by action id."""
    return {action_id: i18n.t(label_key, config) for action_id, label_key in MENU_SPEC}


def _load_icon_image(icon_path: str) -> Any:
    """Load the tray icon as a PIL image, or ``None`` when unavailable."""
    if not HAS_TRAY or not icon_path:
        return None
    candidate = Path(icon_path).expanduser()
    if not candidate.is_file():
        logger.warning("tray icon not found: %s", candidate)
        return None
    try:
        return Image.open(str(candidate)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - icon is best-effort
        logger.debug("could not load tray icon %s: %s", candidate, exc)
        return None


def _generate_default_icon(app_name: str, size: int = 64) -> Any:
    """Generate a default tray icon: the app's initial on a colored tile.

    A sensible fallback when no icon is configured - far better than pystray's
    bare default square. Returns ``None`` only when the ``tray`` extra (Pillow)
    is absent.
    """
    if not HAS_TRAY:
        return None
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGBA", (size, size), (52, 120, 246, 255))
    draw = ImageDraw.Draw(image)
    letter = app_name[0].upper() if app_name else "A"
    try:
        font = ImageFont.load_default(size=int(size * 0.6))  # Pillow >= 10.1
    except TypeError:  # pragma: no cover - older Pillow has no size arg
        font = ImageFont.load_default()
    draw.text((size // 2, size // 2), letter, fill="white", anchor="mm", font=font)
    return image


def _resolve_tray_image(config: LauncherConfig) -> Any:
    """Resolve the tray icon: ``tray_icon_path`` -> ``icon_path`` -> generated default.

    Returns a PIL image (never ``None`` while the ``tray`` extra is present), so
    the tray always shows a sensible icon.
    """
    if not HAS_TRAY:
        return None
    path = config.tray_icon_path or config.icon_path
    image = _load_icon_image(path) if path else None
    if image is not None:
        return image
    return _generate_default_icon(config.app_name)


class TrayController:
    """Owns the ``pystray`` icon lifecycle for the launcher window.

    A no-op when the ``tray`` extra is not installed: :meth:`start` returns
    ``False`` and :meth:`stop` does nothing. Each callback is invoked on the
    pystray thread, so a callback that touches Tk must marshal onto the Tk
    thread itself (the window passes ``after``-wrapped callbacks).
    """

    _READY_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        config: LauncherConfig,
        port: int,
        labels: dict[str, str],
        callbacks: dict[str, Callable[[], Any]],
    ) -> None:
        self._config = config
        self._port = port
        self._labels = labels
        self._callbacks = callbacks
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Show the tray icon. Returns ``True`` only once it is actually visible.

        Returns ``False`` when the extra is missing, no icon image is available,
        the backend is unreliable, or the platform tray never appeared. In every
        False case the caller falls back to closing the window.
        """
        if not HAS_TRAY:
            logger.info("system tray unavailable: the 'tray' extra (pystray + Pillow) is not installed")
            return False
        image = _resolve_tray_image(self._config)
        if image is None:
            return False
        backend = getattr(_TrayIcon, "__module__", "")
        if backend in _UNRELIABLE_BACKENDS:
            logger.info("system tray disabled: backend %s does not dock on modern desktops", backend)
            return False
        tooltip = i18n.t("running", self._config, port=self._port)
        self._icon = _TrayIcon(self._config.app_slug or "launcher", image, tooltip, self._build_menu())
        ready = threading.Event()

        def _on_setup(icon: Any) -> None:
            icon.visible = True
            ready.set()

        def _run_loop() -> None:
            try:
                self._icon.run(setup=_on_setup)
            except Exception as exc:  # noqa: BLE001 - surface, then fall back to close
                logger.warning("system tray loop failed: %s", exc)
                ready.set()

        self._thread = threading.Thread(target=_run_loop, name="dal-tray", daemon=True)
        self._thread.start()

        if not ready.wait(timeout=self._READY_TIMEOUT_SECONDS) or not getattr(self._icon, "visible", False):
            logger.warning("system tray did not appear; falling back to closing the window")
            self.stop()
            return False
        logger.info("minimized to system tray (port %d)", self._port)
        return True

    def stop(self) -> None:
        """Remove the tray icon and end its loop. Safe to call when not started."""
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception as exc:  # noqa: BLE001 - teardown must never raise
            logger.debug("tray icon stop failed: %s", exc)
        self._icon = None
        self._thread = None

    def _build_menu(self) -> Any:
        items = []
        for action_id, _label_key in MENU_SPEC:
            callback = self._callbacks.get(action_id)
            if callback is None:
                continue
            items.append(
                pystray.MenuItem(
                    self._labels.get(action_id, action_id),
                    _as_menu_handler(callback),
                    default=(action_id == "open"),
                )
            )
        return pystray.Menu(*items)


def _as_menu_handler(callback: Callable[[], Any]) -> Callable[..., None]:
    """Adapt a zero-arg callback to pystray's ``handler(icon, item)`` shape."""

    def _handler(_icon: object = None, _item: object = None) -> None:
        callback()

    return _handler
