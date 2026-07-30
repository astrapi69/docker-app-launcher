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
