#!/usr/bin/env python3
"""Assert the frozen binary's rendered contract (#38). Stdlib only.

Reads the ``--render-probe`` JSON from stdin and fails loudly on the frozen-
artifact bug classes that source-tree tests can never see: missing i18n
catalogs (raw key labels), placeholder branding, missing/wrong version.
"""

from __future__ import annotations

import argparse
import json
import sys

# Raw i18n keys that appeared verbatim in broken frozen builds (#34).
RAW_KEYS = {
    "install",
    "open_browser",
    "start",
    "stop",
    "uninstall",
    "apply_port",
    "log_copy",
    "cleanup",
    "run_in_background",
    "about",
    "not_installed",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-version", required=True)
    parser.add_argument("--expect-app-name", required=True)
    args = parser.parse_args()

    contract = json.load(sys.stdin)
    errors: list[str] = []

    title = contract.get("title", "")
    if not title.startswith(args.expect_app_name):
        errors.append(f"branding: title {title!r} does not start with {args.expect_app_name!r}")
    if args.expect_version not in title:
        errors.append(f"version: {args.expect_version!r} missing from title {title!r}")
    if "My App" in title and args.expect_app_name != "My App":
        errors.append(f"placeholder branding in title: {title!r}")

    buttons = contract.get("buttons", {})
    if not buttons:
        errors.append("no buttons rendered")
    raw = {name: label for name, label in buttons.items() if label in RAW_KEYS}
    if raw:
        errors.append(f"raw i18n keys rendered (catalogs not bundled): {raw}")

    first_line = contract.get("first_log_line", "")
    if args.expect_version not in first_line:
        errors.append(f"version missing from first log line: {first_line!r}")

    if errors:
        print("FROZEN BINARY CONTRACT VIOLATIONS:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"frozen contract OK: {title!r}, {len(buttons)} buttons, all labels translated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
