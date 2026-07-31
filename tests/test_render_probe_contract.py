"""The frozen contract's assistant section (#81) - RED proof automated.

assert_render_probe.py is the judge the frozen-binary CI job runs against
the REAL rendered window. These tests prove the judge itself: a pre-#81
contract (no assistant) MUST fail (that is the recorded RED), a complete
one passes, and each structural violation is caught.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent / "frozen" / "assert_render_probe.py"


def _judge(contract: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--expect-version", "1.0.0", "--expect-app-name", "Probe App"],
        input=json.dumps(contract),
        capture_output=True,
        text=True,
    )


def _valid_contract() -> dict[str, object]:
    return {
        "title": "Probe App 1.0.0",
        "buttons": {"install": "Install"},
        "first_log_line": "Probe App 1.0.0",
        "locale": "en",
        "assistant": {
            "elements": [
                "cancel_button",
                "copy_diagnosis_button",
                "copy_support_bundle_button",
                "doctor_checklist",
                "log_toggle",
                "problem_card",
                "status_headline",
                "update_button",
            ],
            "system_check": "Check system",
            "copy_diagnosis": "Copy diagnosis",
            "copy_support_bundle": "Copy support bundle",
            "update_app": "Update",
            "log_toggle": "Show details",
            "cancel": "Cancel",
            "problem_card_sections": ["What does this mean?", "What you can do"],
            "status_headline": "· Probe App is not installed.",
            "log_collapsed_default": True,
            "progress_idle": True,
            "guard_marker_writable": True,
            "guard_marker_dir": "/home/user/.probe-app",
        },
        "exit": {
            "tray_available": False,
            "close_policy_when_running": "quit",
            "exit_paths": ["window_close"],
        },
    }


class TestAssistantContract:
    def test_complete_contract_passes(self) -> None:
        result = _judge(_valid_contract())
        assert result.returncode == 0, result.stdout

    def test_pre_81_contract_fails(self) -> None:
        # The recorded RED: the old contract shape must NOT satisfy the judge.
        contract = _valid_contract()
        del contract["assistant"]
        result = _judge(contract)
        assert result.returncode == 1 and "assistant contract missing" in result.stdout

    def test_missing_element_fails(self) -> None:
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        assistant["elements"].remove("problem_card")
        assert _judge(contract).returncode == 1

    def test_raw_key_label_fails(self) -> None:
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        assistant["system_check"] = "system_check"
        result = _judge(contract)
        assert result.returncode == 1 and "raw i18n key" in result.stdout

    def test_single_explanation_section_fails(self) -> None:
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        assistant["problem_card_sections"] = ["What does this mean?"]
        result = _judge(contract)
        assert result.returncode == 1 and "BOTH explanation sections" in result.stdout

    def test_color_only_headline_fails(self) -> None:
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        assistant["status_headline"] = "running"
        result = _judge(contract)
        assert result.returncode == 1 and "non-color" in result.stdout

    def test_expanded_default_log_fails(self) -> None:
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        assistant["log_collapsed_default"] = False
        assert _judge(contract).returncode == 1

    def test_unwritable_guard_anchor_fails(self) -> None:
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        assistant["guard_marker_writable"] = False
        result = _judge(contract)
        assert result.returncode == 1 and "guard-unavailable" in result.stdout

    def test_pre_marker_contract_fails(self) -> None:
        # RED for the anchor check itself: a probe without the field must
        # not satisfy the judge (an unchecked anchor reads as unchecked).
        contract = _valid_contract()
        assistant = contract["assistant"]
        assert isinstance(assistant, dict)
        del assistant["guard_marker_writable"]
        assert _judge(contract).returncode == 1


class TestExitContract:
    """#108: the judge must reject an artifact without a way out."""

    def test_missing_exit_section_fails(self) -> None:
        contract = _valid_contract()
        del contract["exit"]
        result = _judge(contract)
        assert result.returncode == 1 and "exit contract missing" in result.stdout

    def test_background_without_a_tray_fails(self) -> None:
        """The recorded RED: exactly the device finding, in contract form."""
        contract = _valid_contract()
        contract["exit"] = {
            "tray_available": False,
            "close_policy_when_running": "background",
            "exit_paths": ["window_close"],
        }
        result = _judge(contract)
        assert result.returncode == 1 and "#108 trap" in result.stdout

    def test_background_with_a_tray_passes(self) -> None:
        contract = _valid_contract()
        contract["exit"] = {
            "tray_available": True,
            "close_policy_when_running": "background",
            "exit_paths": ["tray_menu_quit"],
        }
        result = _judge(contract)
        assert result.returncode == 0, result.stdout

    def test_no_exit_path_fails(self) -> None:
        contract = _valid_contract()
        contract["exit"] = {"tray_available": False, "close_policy_when_running": "quit", "exit_paths": []}
        result = _judge(contract)
        assert result.returncode == 1 and "no exit path" in result.stdout

    def test_unknown_policy_fails(self) -> None:
        contract = _valid_contract()
        contract["exit"] = {"tray_available": False, "close_policy_when_running": "maybe", "exit_paths": ["x"]}
        result = _judge(contract)
        assert result.returncode == 1 and "unknown close policy" in result.stdout
