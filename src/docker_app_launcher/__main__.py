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

from docker_app_launcher import __version__, actions, i18n, lockfile, snap
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
    parser.add_argument("--stop", action="store_true", help="Stop the running app and exit.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the app containers/images and exit.")
    parser.add_argument("--cleanup", action="store_true", help="Remove stale leftovers and exit.")
    parser.add_argument("--open", action="store_true", help="Open the app in the browser and exit.")
    parser.add_argument(
        "--render-probe",
        action="store_true",
        help="Render the window once, print its contract (title/labels/log) as JSON, and exit. "
        "Used by the frozen-binary CI check (#38).",
    )
    return parser


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
    }
    app.destroy()
    print(_json.dumps(contract, ensure_ascii=False))
    return 0


def run_cli_action(args: argparse.Namespace, config: LauncherConfig) -> int | None:
    """Route a headless CLI action through the actions layer.

    Returns an exit code when an action flag was handled, or ``None`` when no
    action flag was present (the caller then launches the GUI).

    Exit-code contract (#86, documented in the README): 0 = success,
    1 = the operation failed / the doctor found blockers / health failed,
    2 = config or usage error (raised before this function runs).
    """
    import json as _json

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

    return _launch_window(config, debug=args.debug)


def _launch_window(config: LauncherConfig, *, debug: bool) -> int:
    """Open the persistent window, guarded by a single-instance lockfile.

    A second launch whose lockfile points at a still-running PID is refused
    (the user is told the app is already running) instead of opening a
    duplicate window. Disabled by ``config.single_instance = False``.
    """
    if not config.single_instance:
        from docker_app_launcher.frontends import get_frontend

        return int(get_frontend(config.gui_backend).run(config, debug=debug))
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
        from docker_app_launcher.frontends import get_frontend

        return int(get_frontend(config.gui_backend).run(config, debug=debug))
    finally:
        lockfile.clear_lock(config.lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
