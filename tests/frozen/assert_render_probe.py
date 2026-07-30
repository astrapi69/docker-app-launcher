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

    # Installation assistant (#81): presence + translated labels. The device
    # check judges looks and clarity; completeness is machine work here.
    assistant = contract.get("assistant")
    if not isinstance(assistant, dict):
        errors.append("assistant contract missing entirely (pre-#81 window?)")
    else:
        expected_elements = [
            "copy_diagnosis_button",
            "copy_support_bundle_button",
            "doctor_checklist",
            "log_toggle",
            "problem_card",
            "status_headline",
            "update_button",
        ]
        if assistant.get("elements") != expected_elements:
            errors.append(f"assistant elements mismatch: {assistant.get('elements')!r} != {expected_elements!r}")
        assistant_raw_keys = {
            "system_check",
            "copy_diagnosis",
            "copy_support_bundle",
            "update_app",
            "show_details",
            "hide_details",
            "what_it_means",
            "what_to_do",
        }
        for label_field in ("system_check", "copy_diagnosis", "copy_support_bundle", "update_app", "log_toggle"):
            label = str(assistant.get(label_field, ""))
            if not label.strip():
                errors.append(f"assistant label {label_field} is empty")
            elif label in assistant_raw_keys:
                errors.append(f"assistant label {label_field} shows the raw i18n key {label!r}")
        sections = assistant.get("problem_card_sections", [])
        if len(sections) != 2 or not all(str(s).strip() for s in sections):
            errors.append(f"problem card must render BOTH explanation sections, got {sections!r}")
        elif any(str(s) in assistant_raw_keys for s in sections):
            errors.append(f"problem card sections show raw i18n keys: {sections!r}")
        headline = str(assistant.get("status_headline", ""))
        if not headline or headline[0] not in "✓✗·":
            errors.append(f"status headline must carry a non-color state symbol, got {headline!r}")
        if assistant.get("log_collapsed_default") is not True:
            errors.append("the log must start collapsed (learners see headline/card first)")

    if errors:
        print("FROZEN BINARY CONTRACT VIOLATIONS:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"frozen contract OK: {title!r}, {len(buttons)} buttons, all labels translated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
