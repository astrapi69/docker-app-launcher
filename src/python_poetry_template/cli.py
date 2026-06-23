"""Command-line entry point for python-poetry-template."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from python_poetry_template import __version__


def greet(name: str = "world") -> str:
    """Return a friendly greeting.

    Args:
        name: The name to greet.

    Returns:
        A greeting string.
    """
    return f"Hello, {name}!"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="template-script",
        description="A minimal example CLI shipped with the template.",
    )
    parser.add_argument(
        "-n",
        "--name",
        default="world",
        help="Name to greet (default: world).",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Optional argument list (defaults to ``sys.argv``).

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    print(greet(args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
