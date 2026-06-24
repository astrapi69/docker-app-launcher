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

try:
    import pystray
    from PIL import Image

    HAS_TRAY = True
except Exception:  # noqa: BLE001 - importing pystray can fail beyond ImportError
    # On Linux, importing pystray eagerly selects a backend, which can raise
    # non-ImportError errors when no usable tray is present (headless box, GTK
    # init failure). Any such failure must DISABLE the tray, never crash the
    # launcher on startup.
    pystray = None
    Image = None  # type: ignore[assignment, unused-ignore]
    HAS_TRAY = False

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
        image = _load_icon_image(self._config.icon_path)
        if image is None:
            return False
        backend = getattr(pystray.Icon, "__module__", "")
        if backend in _UNRELIABLE_BACKENDS:
            logger.info("system tray disabled: backend %s does not dock on modern desktops", backend)
            return False
        tooltip = i18n.t("running", self._config, port=self._port)
        self._icon = pystray.Icon(self._config.app_slug or "launcher", image, tooltip, self._build_menu())
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
