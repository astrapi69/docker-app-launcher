"""PySide6 (Qt) frontend: the launcher window on a non-Tk toolkit.

The second reference frontend. It proves the frontend contract holds beyond
the Tk family: worker threads marshal onto the GUI thread with a queued Qt
signal instead of Tk's ``after``, the close button is a ``closeEvent``, and
tooltips/clipboard/progress are Qt-native - while every DECISION (button
layout, per-state enablement, tooltip reasons, action dispatch, close
policy) still comes from the shared :mod:`docker_app_launcher.ui_model`.

Requires the ``qt`` extra (``pip install docker-app-launcher[qt]``); select
it with ``"gui_backend": "qt"`` in the launcher JSON.
"""

from __future__ import annotations

import functools
import logging
import sys
import threading
from typing import Any

from docker_app_launcher import actions, i18n, tray, update_check
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig, locale_for_label
from docker_app_launcher.ui_model import (
    _STATE_KEYS,
    BUTTON_LABELS,
    PRIMARY_BUTTONS,
    PRIMARY_GRID,
    SECONDARY_BUTTONS,
    advanced_ports_visible,
    button_enabled,
    default_internal_ports,
    disabled_reason_key,
    dispatch_action,
    internal_port_fields,
    port_editable,
    should_keep_alive_on_close,
)

logger = logging.getLogger("docker_app_launcher.frontends.qt")

try:
    from PySide6.QtCore import Qt, QTimer, Signal
    from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    HAS_QT = True
except ImportError:  # pragma: no cover - exercised only without the extra
    HAS_QT = False

_OK_STYLE = "color: #188038;"
_ERR_STYLE = "color: #c5221f;"

if HAS_QT:

    class QtLauncherApp(QWidget):
        """The persistent window, rendered with Qt widgets."""

        # Worker threads emit a callable; the queued connection runs it on the
        # GUI thread - Qt's replacement for Tk's ``after(0, fn)``.
        _invoke = Signal(object)

        def __init__(self, config: LauncherConfig, *, debug: bool = False) -> None:
            super().__init__()
            config.resolve()
            self._cfg = config
            self._cfg.locale = actions.resolve_locale(self._cfg)
            self._debug = debug
            self._tray: tray.TrayController | None = None
            self._buttons: dict[str, QPushButton] = {}
            self._invoke.connect(lambda fn: fn())

            self.setWindowTitle(config.app_name)
            self.resize(config.window_width, config.window_height)
            self.setMinimumSize(min(600, config.window_width), min(420, config.window_height))
            if config.icon_path:
                self.setWindowIcon(QIcon(config.icon_path))

            root = QVBoxLayout(self)

            self._state_label = QLabel()
            self._state_label.setStyleSheet("font-size: 15px; font-weight: bold;")
            self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(self._state_label)

            port_row = QHBoxLayout()
            port_row.addStretch()
            port_row.addWidget(QLabel("Port:"))
            self._port_entry = QLineEdit(str(actions.resolve_port(config)))
            self._port_entry.setFixedWidth(80)
            self._port_entry.textEdited.connect(lambda _t: self._validate_port())
            port_row.addWidget(self._port_entry)
            self._port_indicator = QLabel("")
            self._port_indicator.setFixedWidth(20)
            port_row.addWidget(self._port_indicator)
            port_row.addStretch()
            root.addLayout(port_row)

            lang_row = QHBoxLayout()
            lang_row.addStretch()
            lang_row.addWidget(QLabel("🌐"))
            self._locale_combo = QComboBox()
            self._locale_combo.addItems(list(LOCALE_LABELS.values()))
            self._locale_combo.setCurrentText(LOCALE_LABELS.get(self._cfg.locale, self._cfg.locale))
            self._locale_combo.currentTextChanged.connect(lambda _t: self._on_locale_change())
            lang_row.addWidget(self._locale_combo)
            lang_row.addStretch()
            root.addLayout(lang_row)

            self._internal_edits: dict[str, QLineEdit] = {}
            if advanced_ports_visible(config):
                root.addWidget(self._build_advanced_section())

            handlers = self._build_button_handlers()

            self._docker_help = QWidget()
            help_row = QHBoxLayout(self._docker_help)
            help_row.addStretch()
            self._docker_help_row = help_row
            help_row.addStretch()
            self._docker_help.hide()
            root.addWidget(self._docker_help)

            primary = QWidget()
            grid = QGridLayout(primary)
            for name in PRIMARY_BUTTONS:
                row, column = PRIMARY_GRID[name]
                grid.addWidget(self._make_button(name, handlers[name]), row, column)
            root.addWidget(primary, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._copy_log_btn = self._buttons["copy_log"]

            self._progress_box = QWidget()
            progress_layout = QVBoxLayout(self._progress_box)
            progress_layout.setContentsMargins(12, 6, 12, 0)
            self._progress = QProgressBar()
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            progress_layout.addWidget(self._progress)
            self._progress_label = QLabel("")
            progress_layout.addWidget(self._progress_label)
            self._progress_box.hide()
            root.addWidget(self._progress_box)

            self._status = QPlainTextEdit()
            self._status.setReadOnly(True)
            root.addWidget(self._status, stretch=1)

            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            root.addWidget(divider)

            secondary = QWidget()
            secondary_row = QHBoxLayout(secondary)
            for name in SECONDARY_BUTTONS:
                secondary_row.addWidget(self._make_button(name, handlers[name]))
            root.addWidget(secondary, alignment=Qt.AlignmentFlag.AlignHCenter)

            self._refresh()
            if config.cleanup_on_start:
                self._offer_cleanup_if_stale()
            if config.update_check_enabled:
                self._check_for_update()

        # --- thread marshaling ---

        def _post(self, fn: Any) -> None:
            """Run ``fn`` on the GUI thread (queued when called from a worker)."""
            self._invoke.emit(fn)

        # --- pystray adapter (duck-typed Tk root: withdraw / iconify) ---

        def withdraw(self) -> None:
            self.hide()

        def iconify(self) -> None:
            self.showMinimized()

        # --- construction helpers ---

        def _build_button_handlers(self) -> dict[str, Any]:
            return {
                "install": functools.partial(self._on_action, "install"),
                "start": functools.partial(self._on_action, "start"),
                "open_browser": functools.partial(self._on_action, "open"),
                "stop": functools.partial(self._on_action, "stop"),
                "uninstall": functools.partial(self._on_action, "uninstall"),
                "copy_log": self._copy_log,
                "cleanup": self._run_manual_cleanup,
                "background": self._go_background,
                "apply_port": functools.partial(self._on_action, "change_port"),
            }

        def _make_button(self, name: str, handler: Any) -> QPushButton:
            btn = QPushButton(self._t(BUTTON_LABELS[name]))
            btn.setFixedWidth(170)
            btn.clicked.connect(handler)
            self._buttons[name] = btn
            return btn

        def _t(self, key: str, **kwargs: object) -> str:
            return i18n.t(key, self._cfg, **kwargs)

        # --- log ---

        def _log(self, line: str, *, tag: str = "info") -> None:
            self._status.appendPlainText(line)

        def _clear_status(self) -> None:
            self._status.clear()

        def _copy_log(self) -> None:
            content = self._status.toPlainText().strip()
            if not content:
                return
            QGuiApplication.clipboard().setText(content)
            self._copy_log_btn.setText(self._t("log_copied"))
            QTimer.singleShot(2000, lambda: self._copy_log_btn.setText(self._t("log_copy")))

        # --- rendering ---

        def _refresh(self) -> None:
            state = actions.get_state(self._cfg)
            if state == "no_docker":
                self._render_docker_help()
            else:
                heading = self._t(_STATE_KEYS.get(state, "no_docker"), port=actions.resolve_port(self._cfg))
                self._state_label.setText(heading)
                self._docker_help.hide()
            self._port_entry.setEnabled(port_editable(state))
            self._validate_port()
            self._update_button_states(state)

        def _update_button_states(self, state: str) -> None:
            for name, btn in self._buttons.items():
                enabled = button_enabled(state, name)
                btn.setEnabled(enabled)
                reason = disabled_reason_key(name, state)
                btn.setToolTip(self._t(reason) if reason else "")

        def _relabel_buttons(self) -> None:
            for name, btn in self._buttons.items():
                btn.setText(self._t(BUTTON_LABELS[name]))

        def _validate_port(self) -> None:
            raw = self._port_entry.text().strip()
            if not raw.isdigit():
                self._port_indicator.setText("✗")
                self._port_indicator.setStyleSheet(_ERR_STYLE)
                return
            free, _ = actions.check_port(int(raw))
            self._port_indicator.setText("✓" if free else "✗")
            self._port_indicator.setStyleSheet(_OK_STYLE if free else _ERR_STYLE)

        def _render_docker_help(self) -> None:
            # Rebuild the help row's buttons (between the two stretches).
            while self._docker_help_row.count() > 2:
                item = self._docker_help_row.takeAt(1)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.deleteLater()
            info = actions.check_docker_detailed(self._cfg)
            text = info.get("detail") or self._t("no_docker")
            if info.get("command"):
                text += "\n" + info["command"]
            self._state_label.setText(text)
            retry = QPushButton(self._t("retry"))
            retry.clicked.connect(functools.partial(self._on_action, "recheck"))
            self._docker_help_row.insertWidget(1, retry)
            offset = 2
            if info.get("can_start"):
                start_btn = QPushButton(self._t("start_docker"))
                start_btn.clicked.connect(functools.partial(self._start_docker, info))
                self._docker_help_row.insertWidget(offset, start_btn)
                offset += 1
            if not info.get("installed"):
                guide = QPushButton(self._t("open_install_guide"))
                guide.clicked.connect(lambda: actions.open_url(self._cfg.docker_install_url))
                self._docker_help_row.insertWidget(offset, guide)
            self._docker_help.show()

        def _start_docker(self, info: dict[str, Any]) -> None:
            self._set_busy(True)

            def worker() -> None:
                if info.get("platform") == "Linux":
                    result = actions.start_docker_daemon()
                else:
                    result = actions.start_docker_desktop(self._cfg)
                self._post(lambda: self._on_result("start_docker", result))

            threading.Thread(target=worker, daemon=True).start()

        # --- language ---

        def _on_locale_change(self) -> None:
            code = locale_for_label(self._locale_combo.currentText())
            if code is None or code == self._cfg.locale:
                return
            self._cfg.locale = actions.set_locale(self._cfg, code)
            self._relabel_buttons()
            self._refresh()

        # --- advanced internal ports ---

        def _build_advanced_section(self) -> QWidget:
            box = QWidget()
            layout = QVBoxLayout(box)
            for name, label, value in internal_port_fields(self._cfg):
                row = QHBoxLayout()
                row.addStretch()
                row.addWidget(QLabel(label))
                edit = QLineEdit(str(value))
                edit.setFixedWidth(80)
                self._internal_edits[name] = edit
                row.addWidget(edit)
                apply_btn = QPushButton(self._t("apply_port"))
                apply_btn.clicked.connect(functools.partial(self._apply_internal_port, name))
                row.addWidget(apply_btn)
                row.addStretch()
                layout.addLayout(row)
            restore = QPushButton(self._t("restore_defaults"))
            restore.clicked.connect(self._restore_internal_defaults)
            layout.addWidget(restore, alignment=Qt.AlignmentFlag.AlignHCenter)
            return box

        def _apply_internal_port(self, name: str) -> None:
            raw = self._internal_edits[name].text().strip()
            if not raw.isdigit():
                self._log(self._t("port_invalid", min=1, max=65535), tag="err")
                return
            self._set_busy(True)
            port = int(raw)

            def step(label: str) -> None:
                self._post(lambda: self._log(label))

            def worker() -> None:
                result = actions.change_internal_port(self._cfg, name, port, on_step=step, on_output=step)
                self._post(lambda: self._on_result("change_internal_port", result))

            threading.Thread(target=worker, daemon=True).start()

        def _restore_internal_defaults(self) -> None:
            for name, value in default_internal_ports(self._cfg).items():
                if name in self._internal_edits:
                    self._internal_edits[name].setText(str(value))
            self._log(self._t("restore_defaults"))

        # --- update check ---

        def _check_for_update(self) -> None:
            def on_update(tag: str, url: str) -> None:
                self._post(lambda: self._log(self._t("update_available", tag=tag, url=url)))

            update_check.check_for_update_async(self._cfg, on_update)

        # --- cleanup ---

        def _run_manual_cleanup(self) -> None:
            self._log(self._t("cleanup_scanning"))

            def scan() -> None:
                try:
                    stale = actions.find_stale_artifacts(self._cfg)
                except Exception as exc:  # noqa: BLE001 - report, never crash
                    message = str(exc)
                    self._post(lambda: self._log(self._t("error", msg=message), tag="err"))
                    return
                if actions.has_stale_artifacts(stale):
                    self._post(lambda: self._show_cleanup_offer(stale))
                else:
                    self._post(lambda: self._log(self._t("cleanup_none")))

            threading.Thread(target=scan, daemon=True).start()

        def _offer_cleanup_if_stale(self) -> None:
            def scan() -> None:
                try:
                    stale = actions.find_stale_artifacts(self._cfg)
                except Exception:  # noqa: BLE001 - the offer is non-critical
                    return
                if actions.has_stale_artifacts(stale):
                    self._post(lambda: self._show_cleanup_offer(stale))

            threading.Thread(target=scan, daemon=True).start()

        def _show_cleanup_offer(self, stale: dict[str, list[object]]) -> None:
            self._log(self._t("cleanup_found"))
            for line in actions.cleanup_offer_lines(self._cfg, stale):
                self._log("  " + line)
            offer = QWidget()
            offer_row = QHBoxLayout(offer)

            def run_cleanup() -> None:
                offer.deleteLater()
                self._run_cleanup(stale)

            def skip() -> None:
                offer.deleteLater()
                self._log(self._t("cleanup_skipped"))

            run_btn = QPushButton(self._t("cleanup"))
            run_btn.clicked.connect(run_cleanup)
            offer_row.addWidget(run_btn)
            skip_btn = QPushButton(self._t("skip"))
            skip_btn.clicked.connect(skip)
            offer_row.addWidget(skip_btn)
            layout = self.layout()
            assert layout is not None
            layout.addWidget(offer)

        def _run_cleanup(self, stale: dict[str, list[object]]) -> None:
            self._set_busy(True)

            def step(label: str) -> None:
                self._post(lambda: self._log(label))

            def worker() -> None:
                result = actions.cleanup_stale(self._cfg, stale, on_step=step, on_progress=self._on_progress)
                self._post(lambda: self._on_result("cleanup", result))

            threading.Thread(target=worker, daemon=True).start()

        # --- progress ---

        def _on_progress(self, percent: int | None, label: str) -> None:
            self._post(lambda: self._update_progress(percent, label))

        def _update_progress(self, percent: int | None, label: str) -> None:
            self._progress_box.show()
            self._progress_label.setText(label)
            if percent is None:  # indeterminate: unknown duration
                self._progress.setRange(0, 0)
            else:
                self._progress.setRange(0, 100)
                self._progress.setValue(percent)
                if percent >= 100:
                    QTimer.singleShot(2000, self._hide_progress)

        def _hide_progress(self) -> None:
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress_box.hide()

        # --- actions (threaded) ---

        def _on_action(self, action_id: str) -> None:
            raw = self._port_entry.text().strip()
            port = int(raw) if raw.isdigit() else None
            if action_id in ("install", "start") and port is not None:
                actions.set_port(self._cfg, port)
            self._set_busy(True)

            def step(label: str) -> None:
                self._post(lambda: self._log(label))

            def output(line: str) -> None:
                self._post(functools.partial(self._log, line))

            def worker() -> None:
                result = dispatch_action(
                    action_id, self._cfg, port=port, on_step=step, on_output=output, on_progress=self._on_progress
                )
                self._post(lambda: self._on_result(action_id, result))

            threading.Thread(target=worker, daemon=True).start()

        def _on_result(self, action_id: str, result: tuple[bool, str] | None) -> None:
            self._set_busy(False)
            if result is not None:
                ok, msg = result
                self._log(msg, tag="ok" if ok else "err")
                if not ok and self._cfg.on_error is not None:
                    try:
                        self._cfg.on_error(self._cfg, msg)
                    except Exception as exc:  # noqa: BLE001 - hook must never break the UI
                        logger.warning("on_error callback failed: %s", exc)
            self._refresh()

        def _set_busy(self, busy: bool) -> None:
            for btn in self._iter_buttons():
                btn.setEnabled(not busy)
            if busy:
                self._clear_status()
                self._log(self._t("installing"))
            else:
                self.raise_()
                self.activateWindow()

        def _iter_buttons(self) -> list[QPushButton]:
            return self.findChildren(QPushButton)

        # --- close / tray ---

        def closeEvent(self, event: QCloseEvent) -> None:
            keep_alive = should_keep_alive_on_close(
                actions.get_state(self._cfg),
                minimize_enabled=self._cfg.tray_enabled and self._cfg.tray_minimize_on_close,
            )
            if not keep_alive:
                self._stop_tray()
                event.accept()
                return
            event.ignore()
            self._go_background(via_close=True)

        def _background_controller(self) -> tray.TrayController:
            return tray.TrayController(
                config=self._cfg,
                port=actions.resolve_port(self._cfg),
                labels=tray.menu_labels(self._cfg),
                callbacks={
                    "open": lambda: self._post(self._restore_window),
                    "open_browser": lambda: actions.open_browser(self._cfg),
                    "stop": lambda: self._post(lambda: self._on_action("stop")),
                    "quit": lambda: self._post(self._quit),
                },
            )

        def _go_background(self, *, via_close: bool = False) -> None:
            tray.log_diagnostics(self._cfg)
            controller = self._background_controller()
            mode = tray.try_minimize_to_background(self, controller)
            if mode == "tray":
                self._tray = controller
                if not via_close:
                    self._log(self._t("background_tray"))
            else:
                self._log(self._t("closed_minimized") if via_close else self._t("background_iconified"))

        def _restore_window(self) -> None:
            self._stop_tray()
            self.showNormal()
            self.raise_()
            self._refresh()

        def _stop_tray(self) -> None:
            if self._tray is not None:
                self._tray.stop()
                self._tray = None

        def _quit(self) -> None:
            self._stop_tray()
            self.close()


def run(config: LauncherConfig, *, debug: bool = False) -> int:
    """Launch the Qt window. Returns the Qt event-loop exit code."""
    if not HAS_QT:
        raise RuntimeError("the Qt frontend requires the 'qt' extra: pip install docker-app-launcher[qt]")
    app = QApplication.instance() or QApplication(sys.argv)
    window = QtLauncherApp(config, debug=debug)
    window.show()
    return app.exec()
