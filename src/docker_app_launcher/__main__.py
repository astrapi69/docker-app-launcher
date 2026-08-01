"""CLI entry point + GUI router.

With no action flag the persistent window opens. With an action flag
(``--install`` / ``--status`` / ...) the request routes straight through the
:mod:`actions` layer and exits - same code path the GUI uses, so the CLI and
GUI stay in lockstep (CLI<->GUI parity).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from docker_app_launcher import __version__, actions, i18n, lockfile, preview_states, snap, tray, ui_model
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.logging_setup import setup_logging

logger = logging.getLogger("docker_app_launcher")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="docker-app-launcher",
        description="Configurable desktop launcher for Docker-based applications.",
    )
    parser.add_argument(
        # default=None so an EXPLICIT path is distinguishable from the
        # implicit launcher.json lookup: explicit-but-missing is a hard
        # error (#32), the implicit default stays fail-open.
        "--config",
        default=None,
        help="Path to the launcher config JSON (default: launcher.json).",
    )
    parser.add_argument("--port", type=int, default=None, help="Host port for the app (1024-65535).")
    parser.add_argument("--debug", action="store_true", help="Verbose logging to stderr.")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Log level for all sinks (overrides the config's log_level; --debug wins over both).",
    )
    parser.add_argument("--version", action="store_true", help="Print the launcher version and exit.")
    # Headless action flags (CLI<->GUI parity).
    parser.add_argument("--check", action="store_true", help="Check Docker status and exit.")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run every diagnostic (config, Docker, readiness, ports, health) and exit 0/1.",
    )
    parser.add_argument(
        "--status", action="store_true", help="Print the app state (and health, when running) and exit."
    )
    parser.add_argument("--health", action="store_true", help="Probe the app's health endpoint and exit 0/1.")
    parser.add_argument("--app-logs", action="store_true", help="Print the tail of the app container's logs and exit.")
    parser.add_argument(
        "--support-bundle",
        action="store_true",
        help="Print a sanitized, human-readable diagnosis (no env values, no secrets) to paste into a bug report.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable output for --doctor/--status/--health/--support-bundle (stable check ids).",
    )
    parser.add_argument("--install", action="store_true", help="Build + start the app and exit.")
    parser.add_argument("--start", action="store_true", help="Start the stopped app and exit.")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update the app in one step (stop, re-acquire, start, health check) and exit.",
    )
    parser.add_argument("--stop", action="store_true", help="Stop the running app and exit.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the app containers/images and exit.")
    parser.add_argument("--cleanup", action="store_true", help="Remove stale leftovers and exit.")
    parser.add_argument("--open", action="store_true", help="Open the app in the browser and exit.")
    parser.add_argument(
        "--gui-backend",
        metavar="NAME",
        help="Which window toolkit to open for THIS start, overriding the config's gui_backend "
        "(#119). Deliberately not a fixed choice list: frontends can also arrive as entry "
        "points, and a hardcoded list here would refuse a valid one. An unknown name is "
        "refused with the known ones named.",
    )
    parser.add_argument(
        "--render-probe",
        action="store_true",
        help="Render the window once, print its contract (title/labels/log) as JSON, and exit. "
        "Used by the frozen-binary CI check (#38).",
    )
    parser.add_argument(
        "--preview",
        metavar="STATE",
        choices=preview_states.PREVIEW_STATES,
        help="Open the window in a named UI state for LOOKING at it (#115) - touches no Docker and "
        "writes nothing. States:\n" + preview_states.describe_states(),
    )
    return parser


def _probe_guard_marker(config: LauncherConfig) -> bool:
    """Arm and clear the pending marker once - the render probe's proof that
    the guard can work at the BUILT artifact's real config anchor."""
    from docker_app_launcher import lockfile as _lockfile

    detail = _lockfile.write_pending_operation(config, "render-probe")
    _lockfile.clear_pending_operation(config)
    return detail is None


def run_render_probe(config: LauncherConfig) -> int:
    """Open the real window once and print its rendered contract as JSON.

    This is what the frozen-binary CI job (#38) asserts against: a bug that
    only exists in the frozen artifact (missing i18n catalogs, placeholder
    branding, wrong version) is invisible to source-tree tests, so the check
    must interrogate the ACTUAL rendered window of the ACTUAL binary.
    """
    import json as _json

    from docker_app_launcher import frontends

    module = frontends.get_frontend(config.gui_backend)
    app = module.LauncherApp(config) if hasattr(module, "LauncherApp") else None
    if app is None:  # non-tk frontends expose their class differently; tk is the CI target
        print(_json.dumps({"error": f"render probe supports the tk frontend, got {config.gui_backend!r}"}))
        return 2
    app.update()
    contract = {
        "title": app.title(),
        "buttons": {name: str(btn.cget("text")) for name, btn in app._buttons.items()},
        "first_log_line": app._status.get("1.0", "end").splitlines()[0]
        if app._status.get("1.0", "end").strip()
        else "",
        "locale": config.locale,
        # Installation assistant (#81): presence AND translated labels of the
        # new elements are part of the frozen contract - the device check
        # judges looks and clarity, completeness is machine work.
        "assistant": {
            "elements": sorted(app._assistant.keys()),
            "system_check": str(app._system_check_btn.cget("text")),
            "copy_diagnosis": str(app._copy_buttons["copy_diagnosis"].cget("text")),
            "copy_support_bundle": str(app._copy_buttons["copy_support_bundle"].cget("text")),
            "update_app": str(app._update_btn.cget("text")),
            "log_toggle": str(app._log_toggle_btn.cget("text")),
            "cancel": str(app._cancel_btn.cget("text")),
            "problem_card_sections": [
                str(app._problem_meaning_label.cget("text")),
                str(app._problem_fix_label.cget("text")),
            ],
            "status_headline": str(app._state_label.cget("text")),
            "log_collapsed_default": bool(app._log_collapsed),
            # #97: the idle end state EXISTS - at render nothing runs, so the
            # progress indicator must be hidden; a bar that is visible here
            # is the stuck-activity class the device finding exposed.
            "progress_idle": not bool(app._progress_frame.winfo_ismapped()),
            # #102/#103 at the BUILT artifact: the concurrency guard's marker
            # must be armable where the frozen binary actually runs - config
            # paths are anchored specially in frozen operation, and a wrong
            # anchor would greet the device session with the guard-
            # unavailable note. Probed by ARMING for real, then cleaning up.
            "guard_marker_writable": _probe_guard_marker(config),
            "guard_marker_dir": str(config.config_path),
        },
        # #108 at the BUILT artifact: the tray is what decides whether the X
        # closes the launcher or backgrounds it. The frozen bundle ships
        # without the tray extra ON PURPOSE, so the artifact must report a
        # close policy of "quit" - a "background" here would be the device
        # finding again (window only endable through the task manager).
        "exit": {
            "tray_available": tray.tray_available(),
            "close_policy_when_running": (
                "background"
                if ui_model.should_keep_alive_on_close(
                    "running",
                    minimize_enabled=config.tray_enabled and config.tray_minimize_on_close,
                    tray_available=tray.tray_available(),
                )
                else "quit"
            ),
            "exit_paths": sorted(ui_model.exit_paths_for("tray_available" if tray.tray_available() else "no_tray")),
        },
    }
    app.destroy()
    print(_json.dumps(contract, ensure_ascii=False))
    return 0


# CLI flags that trigger long-running actions, mapped to their action ids -
# ONE source pinned against ui_model.LONG_RUNNING_ACTIONS by
# tests/test_pending_operation_guard.py, so a new entry path cannot slip
# past the guard unnoticed (#102). change_port/change_internal_port have no
# standalone CLI action flag (documented exception in the pin).
GUARDED_CLI_ACTIONS = {
    "install": "install",
    "start": "start",
    "update": "update",
    "stop": "stop",
    "uninstall": "uninstall",
    "cleanup": "cleanup",
}


def run_cli_action(args: argparse.Namespace, config: LauncherConfig) -> int | None:
    """Route a headless CLI action through the actions layer.

    Returns an exit code when an action flag was handled, or ``None`` when no
    action flag was present (the caller then launches the GUI).

    Exit-code contract (#86, documented in the README): 0 = success,
    1 = the operation failed / the doctor found blockers / health failed,
    2 = config or usage error (raised before this function runs).
    """
    import json as _json

    from docker_app_launcher import ui_model as _ui_model

    # The concurrency guard holds on BOTH entry paths (#102): the same gate
    # the GUI consults. A pending unresponsive operation blocks the action;
    # a release by TTL prints the never-confirmed note and proceeds.
    for flag_attr, guarded_action in GUARDED_CLI_ACTIONS.items():
        if getattr(args, flag_attr):
            block, note = _ui_model.check_pending_operation(config, guarded_action)
            if block is not None:
                print(block)
                return 1
            if note is not None:
                print(note)
            break

    if args.doctor:
        from docker_app_launcher.doctor import collect_doctor_report, render_doctor_text

        report = collect_doctor_report(config)
        print(_json.dumps(report.to_dict(), ensure_ascii=False) if args.json else render_doctor_text(report))
        return 0 if (report.ok and report.complete) else 1
    if args.check:
        ok, msg = actions.check_docker()
        print(msg)
        return 0 if ok else 1
    if args.status:
        from docker_app_launcher.doctor import collect_status_report

        status = collect_status_report(config)
        print(_json.dumps(status.to_dict(), ensure_ascii=False) if args.json else status.to_text())
        return 0
    if args.health:
        from docker_app_launcher.doctor import collect_health_report

        health = collect_health_report(config)
        print(_json.dumps(health.to_dict(), ensure_ascii=False) if args.json else f"{health.url} -> {health.detail}")
        return 0 if health.ok else 1
    if args.app_logs:
        ok, text = actions.app_logs(config)
        print(text)
        return 0 if ok else 1
    if args.support_bundle:
        from docker_app_launcher.doctor import collect_support_bundle

        bundle = collect_support_bundle(config)
        print(_json.dumps(bundle.to_dict(), ensure_ascii=False) if args.json else bundle.to_text())
        return 0
    if args.install:
        ok, msg = actions.install(config, on_step=print, on_output=print)
        print(msg)
        return 0 if ok else 1
    if args.start:
        ok, msg = actions.start(config, on_step=print, on_output=print)
        print(msg)
        return 0 if ok else 1
    if args.update:
        ok, msg = actions.update(config, on_step=print, on_output=print)
        print(msg)
        return 0 if ok else 1
    if args.stop:
        ok, msg = actions.stop(config)
        print(msg)
        return 0 if ok else 1
    if args.uninstall:
        ok, msg = actions.uninstall(config, on_step=print)
        print(msg)
        return 0 if ok else 1
    if args.cleanup:
        stale = actions.find_stale_artifacts(config)
        ok, msg = actions.cleanup_stale(config, stale, on_step=print)
        print(msg)
        return 0 if ok else 1
    if args.open:
        actions.open_browser(config)
        return 0
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    if args.version:
        print(f"docker-app-launcher {__version__}")
        return 0

    try:
        config = LauncherConfig.from_json(args.config or "launcher.json", require=args.config is not None)
    except FileNotFoundError as exc:
        # A wrong explicit path used to silently launch an all-defaults
        # "My App" window - only strace found the cause (#32).
        print(f"Error: {exc}", file=sys.stderr)
        logger.error("%s", exc)
        return 2
    if args.log_level:
        config.log_level = args.log_level
    setup_logging(config, debug=args.debug)
    snap.log_confinement_warning()  # surface Snap sandbox path limits (G7, #63)

    if args.gui_backend:
        # Validated EAGERLY, even for a CLI-only run: a typo that is silently
        # ignored today would be discovered the next time someone opens the
        # window, far from the command that caused it.
        config.gui_backend = args.gui_backend
        refusal = _frontend_refusal(config.gui_backend)
        if refusal is not None:
            print(f"Error: {refusal}", file=sys.stderr)
            logger.error("%s", refusal)
            return 2

    if args.port is not None:
        ok, msg = actions.set_port(config, args.port)
        if not ok:
            print(msg, file=sys.stderr)
            return 2

    action_rc = run_cli_action(args, config)
    if action_rc is not None:
        return action_rc

    if args.render_probe:
        return run_render_probe(config)

    if args.preview:
        return run_preview(config, args.preview, debug=args.debug)

    return _launch_window(config, debug=args.debug)


def _frontend_refusal(name: str) -> str | None:
    """Why ``name`` cannot be used, or ``None`` when it can (#119).

    Resolution only - the module is imported, not run, so this stays cheap
    and side-effect-free. Whether the toolkit is actually INSTALLED is a
    different question: ctk/qt import fine without their extra and refuse in
    ``run()``, which :func:`_open_frontend` turns into the same clean
    message. A selection that leads nowhere is worse than no selection.
    """
    from docker_app_launcher.frontends import get_frontend

    try:
        get_frontend(name)
    except (ValueError, TypeError) as exc:
        return str(exc)
    return None


def _open_frontend(config: LauncherConfig, *, debug: bool, preview_state: str | None = None) -> int:
    """Open the configured frontend, translating a missing extra into a message.

    Without this the RuntimeError from ``run()`` - which already carries the
    exact ``pip install docker-app-launcher[qt]`` hint - reached the user as a
    traceback, which reads like a crash rather than like an instruction.
    """
    from docker_app_launcher.frontends import get_frontend

    try:
        return int(get_frontend(config.gui_backend).run(config, debug=debug, preview_state=preview_state))
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        logger.error("frontend %s unusable: %s", config.gui_backend, exc)
        return 2


def run_preview(config: LauncherConfig, state: str, *, debug: bool) -> int:
    """Open the window in ``state`` and hold it (#115).

    Deliberately NOT routed through :func:`_launch_window`: that takes the
    single-instance lockfile, and a looking tool must not write - nor refuse
    to open because the real launcher is running, which is exactly when you
    want to compare the two.
    """
    # Printed WITH the preview, so nobody mistakes a fed state for a real one.
    # flush: stdout is block-buffered as soon as it is redirected, and the
    # screenshot harness (#116) kills this process once it has its image - the
    # honesty note would then be the one thing lost, in exactly the situation
    # it exists for.
    print(preview_states.state_note(state), flush=True)
    return _open_frontend(config, debug=debug, preview_state=state)


def _launch_window(config: LauncherConfig, *, debug: bool) -> int:
    """Open the persistent window, guarded by a single-instance lockfile.

    A second launch whose lockfile points at a still-running PID is refused
    (the user is told the app is already running) instead of opening a
    duplicate window. Disabled by ``config.single_instance = False``.
    """
    if not config.single_instance:
        return _open_frontend(config, debug=debug)
    if lockfile.another_instance_alive(config.lock_path):
        # Ask the running window to come to the foreground (#31) - the
        # refusal notice alone left the user searching for the window.
        lockfile.request_focus(config.lock_path)
        message = i18n.t("already_running", config)
        print(message)
        logger.info("second instance refused: %s", message)
        return 0
    lockfile.write_lock(config.lock_path)
    try:
        return _open_frontend(config, debug=debug)
    finally:
        lockfile.clear_lock(config.lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
