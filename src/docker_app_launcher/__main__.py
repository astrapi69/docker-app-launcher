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

from docker_app_launcher import __version__, actions, i18n, lockfile
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
        "--config", default="launcher.json", help="Path to the launcher config JSON (default: launcher.json)."
    )
    parser.add_argument("--port", type=int, default=None, help="Host port for the app (1024-65535).")
    parser.add_argument("--debug", action="store_true", help="Verbose logging to stdout.")
    parser.add_argument("--version", action="store_true", help="Print the launcher version and exit.")
    # Headless action flags (CLI<->GUI parity).
    parser.add_argument("--check", action="store_true", help="Check Docker status and exit.")
    parser.add_argument("--status", action="store_true", help="Print the app state and exit.")
    parser.add_argument("--install", action="store_true", help="Build + start the app and exit.")
    parser.add_argument("--start", action="store_true", help="Start the stopped app and exit.")
    parser.add_argument("--stop", action="store_true", help="Stop the running app and exit.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the app containers/images and exit.")
    parser.add_argument("--cleanup", action="store_true", help="Remove stale leftovers and exit.")
    parser.add_argument("--open", action="store_true", help="Open the app in the browser and exit.")
    return parser


def run_cli_action(args: argparse.Namespace, config: LauncherConfig) -> int | None:
    """Route a headless CLI action through the actions layer.

    Returns an exit code when an action flag was handled, or ``None`` when no
    action flag was present (the caller then launches the GUI).
    """
    if args.check:
        ok, msg = actions.check_docker()
        print(msg)
        return 0 if ok else 1
    if args.status:
        print(f"Status: {actions.get_state(config)}")
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

    config = LauncherConfig.from_json(args.config)
    setup_logging(config, debug=args.debug)

    if args.port is not None:
        ok, msg = actions.set_port(config, args.port)
        if not ok:
            print(msg, file=sys.stderr)
            return 2

    action_rc = run_cli_action(args, config)
    if action_rc is not None:
        return action_rc

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
