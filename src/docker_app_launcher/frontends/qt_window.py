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

import contextlib
import functools
import logging
import platform as _platform
import re
import sys
import threading
from typing import Any

from docker_app_launcher import actions, i18n, lockfile, tray, ui_model, update_check
from docker_app_launcher.config import LOCALE_LABELS, LauncherConfig, locale_for_label
from docker_app_launcher.frontends.tk_window import ASSISTANT_WIDGET_BUILDERS
from docker_app_launcher.ui_model import (
    _STATE_KEYS,
    BUTTON_LABELS,
    PRIMARY_BUTTONS,
    PRIMARY_GRID,
    SECONDARY_BUTTONS,
    about_lines,
    advanced_ports_visible,
    button_enabled,
    default_internal_ports,
    disabled_reason_key,
    dispatch_action,
    initial_focus_button,
    internal_port_fields,
    issue_tracker_url,
    log_panel_line,
    port_editable,
    run_guarded,
    should_keep_alive_on_close,
    window_title,
)

logger = logging.getLogger("docker_app_launcher.frontends.qt_window")

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
            self._log_follow_stop: threading.Event | None = None
            self._cancel_build = threading.Event()
            self._current_action: str | None = None
            self._build_in_progress = False
            self._invoke.connect(lambda fn: fn())

            self.setWindowTitle(window_title(config))
            self.resize(config.window_width, config.window_height)
            stored_geometry = actions.resolve_window_geometry(config)
            match = re.match(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$", stored_geometry)
            if match:
                self.resize(int(match.group(1)), int(match.group(2)))
                self.move(int(match.group(3)), int(match.group(4)))
            if not config.window_resizable:
                # Parity with the tk/ctk frontends: honor the opt-out.
                # setFixedSize pins min=max=current; no setMinimumSize after
                # it - that would re-loosen the minimum.
                self.setFixedSize(self.width(), self.height())
            else:
                self.setMinimumSize(min(600, config.window_width), min(420, config.window_height))
            if config.icon_path:
                self.setWindowIcon(QIcon(config.icon_path))

            root = QVBoxLayout(self)

            self._state_label = QLabel()
            self._state_label.setStyleSheet("font-size: 15px; font-weight: bold;")
            self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # The wordiest state text (docker_no_permission + usermod command,
            # #47) must wrap instead of clipping at the window edge.
            self._state_label.setWordWrap(True)
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

            # Installation assistant (#81): identical element set as tk/ctk,
            # enforced by tests/test_frontend_parity.py. The assistant row
            # sits under the secondary actions; the problem card inserts
            # itself above the (collapsible) log.
            self._assistant_labels = ui_model.assistant_labels(config)
            self._root_layout = root
            assistant = QWidget()
            self._assistant_row = QHBoxLayout(assistant)
            self._assistant: dict[str, Any] = {}
            for element, builder in ASSISTANT_WIDGET_BUILDERS.items():
                self._assistant[element] = getattr(self, builder)()
            root.addWidget(assistant, alignment=Qt.AlignmentFlag.AlignHCenter)

            self._log(f"{about_lines(config)[0]} · {config.gui_backend} · {_platform.system()}")
            self._refresh()
            if config.cleanup_on_start:
                self._offer_cleanup_if_stale()
            if config.update_check_enabled:
                self._check_for_update()
            if config.single_instance:
                # A refused second launch drops a focus marker (#31).
                self._focus_timer = QTimer(self)
                self._focus_timer.timeout.connect(self._poll_focus_request)
                self._focus_timer.start(1000)

        def _on_app_logs(self) -> None:
            """App-logs button (#72): one-shot tail normally; while RUNNING it
            toggles a live follow - no busy state."""
            if self._log_follow_stop is not None:
                self._log_follow_stop.set()
                return
            if getattr(self, "_focused_state", None) == "running":
                self._start_log_follow()
            else:
                self._on_action("app_logs")

        def _start_log_follow(self) -> None:
            stop = threading.Event()
            self._log_follow_stop = stop
            self._buttons["app_logs"].setText(self._t("app_logs_follow_stop"))

            def marshal_line(line: str) -> None:
                self._post(functools.partial(self._log, line))

            def worker() -> None:
                result = run_guarded(
                    "app_logs_follow",
                    lambda: actions.stream_app_logs(
                        self._cfg,
                        on_line=marshal_line,
                        should_stop=stop.is_set,
                    ),
                )
                self._post(functools.partial(self._end_log_follow, result))

            threading.Thread(target=worker, daemon=True, name="dal-gui-log-follow").start()

        def _end_log_follow(self, result: tuple[bool, str] | None) -> None:
            self._log_follow_stop = None
            self._buttons["app_logs"].setText(self._t(BUTTON_LABELS["app_logs"]))
            if result is not None and not result[0]:
                self._log(result[1], tag="err")

        def _stop_log_follow_if_running(self) -> None:
            if self._log_follow_stop is not None:
                self._log_follow_stop.set()

        def _poll_focus_request(self) -> None:
            """Bring the window up when a second launch asked for focus (#31)."""
            if lockfile.consume_focus_request(self._cfg.lock_path):
                self._bring_to_front()

        def _bring_to_front(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

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
                "uninstall": self._confirm_uninstall,
                "copy_log": self._copy_log,
                "cleanup": self._run_manual_cleanup,
                "app_logs": self._on_app_logs,
                "background": self._go_background,
                "apply_port": functools.partial(self._on_action, "change_port"),
                "info": self._show_about,
            }

        def _make_button(self, name: str, handler: Any) -> QPushButton:
            btn = QPushButton(self._t(BUTTON_LABELS[name]))
            btn.setFixedWidth(170)
            btn.clicked.connect(handler)
            self._buttons[name] = btn
            return btn

        # --- installation assistant (#81) ---

        def _build_status_headline(self) -> Any:
            return self._state_label

        def _apply_status_headline(self, state: str, *, health_ok: bool | None = None) -> None:
            severity, text = ui_model.status_headline(self._cfg, state, health_ok=health_ok)
            colors = {"ok": "#188038", "error": "#c5221f", "info": ""}
            color = f" color: {colors[severity]};" if colors[severity] else ""
            self._state_label.setStyleSheet(f"font-size: 15px; font-weight: bold;{color}")
            self._headline_symbol = text.split(" ", 1)[0]

        def _build_doctor_checklist(self) -> Any:
            btn = QPushButton(self._assistant_labels["system_check"])
            btn.clicked.connect(self._on_system_check)
            self._assistant_row.addWidget(btn)
            self._system_check_btn = btn
            return btn

        def _on_system_check(self) -> None:
            from docker_app_launcher.doctor import collect_doctor_report

            self._system_check_btn.setEnabled(False)

            def _run() -> None:
                report = collect_doctor_report(self._cfg)
                self._post(functools.partial(self._render_doctor, report))

            threading.Thread(target=_run, daemon=True, name="dal-gui-doctor").start()

        def _render_doctor(self, report: Any) -> bool:
            self._system_check_btn.setEnabled(True)
            self._set_log_collapsed(False)
            for status, line in ui_model.doctor_checklist_rows(report):
                self._log(line, tag={"ok": "ok", "error": "err"}.get(status, "info"))
            card = ui_model.primary_problem(self._cfg, report)
            if card is None:
                self._log(self._assistant_labels["no_problems_found"], tag="ok")
                self._hide_problem_card()
            else:
                self._show_problem_card(card)
            return True

        def _build_problem_card(self) -> Any:
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            card_layout = QVBoxLayout(frame)
            self._problem_title = QLabel()
            self._problem_title.setStyleSheet("font-weight: bold;")
            self._problem_title.setWordWrap(True)
            card_layout.addWidget(self._problem_title)
            self._problem_message = QLabel()
            self._problem_message.setWordWrap(True)
            card_layout.addWidget(self._problem_message)
            self._problem_meaning_label = QLabel(self._assistant_labels["what_it_means"])
            self._problem_meaning_label.setStyleSheet("font-weight: bold;")
            card_layout.addWidget(self._problem_meaning_label)
            self._problem_meaning = QLabel()
            self._problem_meaning.setWordWrap(True)
            card_layout.addWidget(self._problem_meaning)
            self._problem_fix_label = QLabel(self._assistant_labels["what_to_do"])
            self._problem_fix_label.setStyleSheet("font-weight: bold;")
            card_layout.addWidget(self._problem_fix_label)
            self._problem_fix = QLabel()
            self._problem_fix.setWordWrap(True)
            card_layout.addWidget(self._problem_fix)
            frame.hide()
            self._problem_frame = frame
            self._root_layout.insertWidget(self._root_layout.indexOf(self._status), frame)
            return frame

        def _show_problem_card(self, card: dict[str, str]) -> None:
            self._problem_title.setText(f"✗ {card['title']}: {card['id']}")
            self._problem_message.setText(card["message"])
            self._problem_meaning.setText(card["meaning"])
            self._problem_fix.setText(card["fix"])
            self._problem_frame.show()

        def _hide_problem_card(self) -> None:
            self._problem_frame.hide()

        def _build_copy_diagnosis_button(self) -> Any:
            btn = QPushButton(self._assistant_labels["copy_diagnosis"])
            btn.clicked.connect(lambda: self._copy_with_feedback("copy_diagnosis", ui_model.diagnosis_clipboard_text))
            self._assistant_row.addWidget(btn)
            self._copy_buttons = getattr(self, "_copy_buttons", {})
            self._copy_buttons["copy_diagnosis"] = btn
            return btn

        def _build_copy_support_bundle_button(self) -> Any:
            btn = QPushButton(self._assistant_labels["copy_support_bundle"])
            btn.clicked.connect(
                lambda: self._copy_with_feedback("copy_support_bundle", ui_model.support_bundle_clipboard_text)
            )
            self._assistant_row.addWidget(btn)
            self._copy_buttons["copy_support_bundle"] = btn
            return btn

        def _copy_with_feedback(self, label_key: str, text_fn: Any) -> None:
            button = self._copy_buttons[label_key]
            button.setEnabled(False)

            def _run() -> None:
                text = text_fn(self._cfg)

                def done() -> None:
                    QGuiApplication.clipboard().setText(text)
                    button.setText(self._assistant_labels["copied_to_clipboard"])
                    button.setEnabled(True)
                    QTimer.singleShot(2000, lambda: button.setText(self._assistant_labels[label_key]))

                self._post(done)

            threading.Thread(target=_run, daemon=True, name=f"dal-gui-{label_key}").start()

        def _build_update_button(self) -> Any:
            """One-step update (#92): stop -> re-acquire -> start -> health,
            through the shared action machinery. The action self-guards."""
            btn = QPushButton(self._assistant_labels["update_app"])
            btn.clicked.connect(functools.partial(self._on_action, "update"))
            self._assistant_row.addWidget(btn)
            self._update_btn = btn
            return btn

        def _build_log_toggle(self) -> Any:
            btn = QPushButton()
            btn.clicked.connect(self._toggle_log)
            self._assistant_row.addWidget(btn)
            self._log_toggle_btn = btn
            self._log_collapsed = False
            self._set_log_collapsed(True)
            return btn

        def _toggle_log(self) -> None:
            self._set_log_collapsed(not self._log_collapsed)

        def _set_log_collapsed(self, collapsed: bool) -> None:
            self._log_collapsed = collapsed
            if collapsed:
                self._status.hide()
                self._log_toggle_btn.setText(self._assistant_labels["show_details"])
            else:
                self._status.show()
                self._log_toggle_btn.setText(self._assistant_labels["hide_details"])

        def _build_cancel_button(self) -> Any:
            btn = QPushButton(self._assistant_labels["cancel_operation"])
            btn.clicked.connect(self._on_cancel_click)
            btn.hide()
            layout = self._progress_box.layout()
            assert layout is not None  # built in __init__ before the assistant
            layout.addWidget(btn)
            self._cancel_btn = btn
            self._cancel_watchdog: QTimer | None = None
            return btn

        def _show_cancel_for(self, action_id: str) -> None:
            if action_id in ui_model.CANCELLABLE_ACTIONS:
                self._progress_box.show()
                self._cancel_btn.setText(self._assistant_labels["cancel_operation"])
                self._cancel_btn.setEnabled(True)
                self._cancel_btn.show()
            else:
                self._cancel_btn.hide()

        def _on_cancel_click(self) -> None:
            self._cancel_build.set()
            self._cancel_btn.setText(self._assistant_labels["cancelling"])
            self._cancel_btn.setEnabled(False)
            current = self._current_action or ""
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._on_cancel_unresponsive(current))
            timer.start(ui_model.CANCEL_WATCHDOG_SECONDS * 1000)
            self._cancel_watchdog = timer

        def _cancel_watchdog_stop(self) -> None:
            watchdog = getattr(self, "_cancel_watchdog", None)
            if watchdog is not None:
                watchdog.stop()
                self._cancel_watchdog = None

        def _pending_background_blocks(self, action_id: str) -> bool:
            """ONE gate for both entry paths (#102): delegates to
            ui_model.check_pending_operation - the same call the CLI makes, so
            the two ways cannot drift (mirror class of the bundle finding). A
            release by TTL logs the never-confirmed note and proceeds."""
            block, note = ui_model.check_pending_operation(self._cfg, action_id)
            if block is not None:
                self._log(block, tag="err")
                return True
            if note is not None:
                self._log(note, tag="err")
            return False

        def _on_cancel_unresponsive(self, action_id: str) -> None:
            self._cancel_watchdog = None
            self._hide_progress()
            self._build_in_progress = False
            self._set_busy(False)
            lockfile.write_pending_operation(self._cfg, action_id)
            self._log(self._t("cancel_unresponsive"), tag="err")
            self._record_cancel_outcome(action_id, "cancel_unresponsive")
            self._refresh()

        def _record_cancel_outcome(self, action_id: str, outcome: str) -> None:
            from docker_app_launcher.install_manifest import record_operation_outcome

            record_operation_outcome(self._cfg, action_id or "unknown", outcome)

        def _t(self, key: str, **kwargs: object) -> str:
            return i18n.t(key, self._cfg, **kwargs)

        # --- log ---

        def _log(self, line: str, *, tag: str = "info") -> None:
            if tag == "err" and getattr(self, "_log_collapsed", False):
                self._set_log_collapsed(False)  # errors must never hide behind the toggle
            log_panel_line(line, tag)
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
                self._apply_status_headline(state)
                self._state_label.setText(f"{self._headline_symbol} {heading}")
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
            self._apply_initial_focus(state)
            if state != "running":
                self._stop_log_follow_if_running()

        def _apply_initial_focus(self, state: str) -> None:
            """Keyboard focus lands on the state's primary action (#31) - only
            on a real state CHANGE, never on polling refreshes. Fusion draws
            the focus frame itself."""
            if state == getattr(self, "_focused_state", None):
                return
            self._focused_state = state
            target = initial_focus_button(state)
            if button_enabled(state, target):
                self._buttons[target].setFocus()

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
            info = actions.check_docker_detailed(self._cfg, on_step=self._log)
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
            if info.get("can_fix_permission"):
                fix_btn = QPushButton(self._t("fix_docker_permission"))
                fix_btn.clicked.connect(self._fix_docker_permission)
                self._docker_help_row.insertWidget(offset, fix_btn)
                offset += 1
            if not info.get("installed"):
                guide = QPushButton(self._t("open_install_guide"))
                guide.clicked.connect(lambda: actions.open_url(self._cfg.docker_install_url))
                self._docker_help_row.insertWidget(offset, guide)
            self._docker_help.show()

        def _show_about(self) -> None:
            from PySide6.QtWidgets import QMessageBox

            text = "\n".join(about_lines(self._cfg)) + "\n\n" + self._t("about_open_issues")
            answer = QMessageBox.question(
                self,
                self._t("about"),
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                actions.open_url(issue_tracker_url(self._cfg))

        def _confirm_uninstall(self) -> None:
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self,
                self._cfg.app_name,
                self._t("confirm_uninstall", app=self._cfg.app_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._on_action("uninstall")

        def _fix_docker_permission(self) -> None:
            """Self-repair for the docker-group case (#27): confirm (docker
            group = effectively root), then pkexec usermod off-thread."""
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self,
                self._t("fix_docker_permission"),
                self._t("docker_group_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._log(self._t("docker_group_cancelled"))
                return
            self._set_busy(True)

            def worker() -> None:
                result = run_guarded("fix_permission", lambda: actions.add_user_to_docker_group(self._cfg))
                self._post(lambda: self._on_result("fix_permission", result))

            threading.Thread(target=worker, daemon=True).start()

        def _start_docker(self, info: dict[str, Any]) -> None:
            self._set_busy(True)

            def worker() -> None:
                def body() -> tuple[bool, str]:
                    if info.get("platform") == "Linux":
                        result = actions.start_docker_daemon()
                    else:
                        result = actions.start_docker_desktop(self._cfg)
                    if result[0]:  # started - now wait for the daemon (VM boot, #28)
                        result = actions.wait_for_docker(self._cfg, on_progress=self._on_progress)
                        self._post(self._hide_progress)
                    return result

                result = run_guarded("start_docker", body)
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
                result = run_guarded(
                    "change_internal_port",
                    lambda: actions.change_internal_port(self._cfg, name, port, on_step=step, on_output=step),
                )
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

            run_btn = QPushButton(self._t("cleanup_now"))
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
                result = run_guarded(
                    "cleanup",
                    lambda: actions.cleanup_stale(self._cfg, stale, on_step=step, on_progress=self._on_progress),
                )
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
            if getattr(self, "_cancel_btn", None) is not None:
                self._cancel_btn.hide()
            self._progress_box.hide()

        # --- actions (threaded) ---

        def _on_action(self, action_id: str) -> None:
            if self._pending_background_blocks(action_id):
                return
            raw = self._port_entry.text().strip()
            port = int(raw) if raw.isdigit() else None
            if action_id in ("install", "start") and port is not None:
                actions.set_port(self._cfg, port)
            cancellable = action_id in ui_model.CANCELLABLE_ACTIONS
            if cancellable:
                self._cancel_build.clear()
                self._build_in_progress = True
            self._current_action = action_id
            self._set_busy(True)
            self._show_cancel_for(action_id)

            def step(label: str) -> None:
                self._post(lambda: self._log(label))

            def output(line: str) -> None:
                self._post(functools.partial(self._log, line))

            def worker() -> None:
                result = run_guarded(
                    action_id,
                    lambda: dispatch_action(
                        action_id,
                        self._cfg,
                        port=port,
                        on_step=step,
                        on_output=output,
                        on_progress=self._on_progress,
                        should_cancel=self._cancel_build.is_set if action_id in ui_model.CANCELLABLE_ACTIONS else None,
                    ),
                )
                self._post(lambda: self._on_result(action_id, result))

            threading.Thread(target=worker, daemon=True).start()

        def _on_result(self, action_id: str, result: tuple[bool, str] | None) -> None:
            # DEFINED end state for EVERY outcome (#97): stop and hide the
            # progress indicator on success, failure and cancel alike.
            self._cancel_watchdog_stop()
            lockfile.clear_pending_operation(self._cfg)  # the late result IS the guard's exit (#100)
            self._hide_progress()
            if result is not None and not result[0] and self._cancel_build.is_set():
                self._record_cancel_outcome(action_id, "cancelled")
            self._build_in_progress = False
            self._set_busy(False)
            if result is not None:
                ok, msg = result
                self._log(msg, tag="ok" if ok else "err")
                if ok and self._cancel_build.is_set():
                    # The cancel came too late (#98 review): name it, or the
                    # user who cancelled is confused by a success message.
                    self._log(self._t("completed_before_cancel"))
                if not ok and self._cfg.on_error is not None:
                    try:
                        self._cfg.on_error(self._cfg, msg)
                    except Exception as exc:  # noqa: BLE001 - hook must never break the UI
                        logger.warning("on_error callback failed: %s", exc)
            self._refresh()

        def _set_busy(self, busy: bool) -> None:
            for btn in self._iter_buttons():
                btn.setEnabled(not busy)
            if busy and getattr(self, "_cancel_btn", None) is not None and self._cancel_btn.isVisible():
                self._cancel_btn.setEnabled(True)  # the one way OUT of busy stays clickable
            if busy:
                self._clear_status()
                self._log(self._t("installing"))
            else:
                self.raise_()
                self.activateWindow()

        def _iter_buttons(self) -> list[QPushButton]:
            return self.findChildren(QPushButton)

        # --- close / tray ---

        def _dal_close_hook(self) -> None:
            self._stop_log_follow_if_running()

        def closeEvent(self, event: QCloseEvent) -> None:
            self._dal_close_hook()
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
            with contextlib.suppress(Exception):
                actions.set_window_geometry(self._cfg, f"{self.width()}x{self.height()}+{self.x()}+{self.y()}")
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
