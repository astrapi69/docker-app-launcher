"""Tests for the ui_model assistant presentation layer (#81 phase a)."""

from __future__ import annotations

from docker_app_launcher import ui_model
from docker_app_launcher.diagnostics_report import CheckResult, DoctorReport


class TestAssistantPresentationLayer:
    """#81 phase a: the single source of structure and texts for the
    installation assistant - renderers decide presentation, never content."""

    def _report(self, *checks: tuple[str, str, str]) -> DoctorReport:
        return DoctorReport(app_name="X", mode="compose", checks=[CheckResult(*c) for c in checks])

    def test_labels_are_localized_and_complete(self, config) -> None:
        labels = ui_model.assistant_labels(config)
        expected = {
            "system_check",
            "copy_diagnosis",
            "copy_support_bundle",
            "copied_to_clipboard",
            "problem_found",
            "what_it_means",
            "what_to_do",
            "no_problems_found",
            "show_details",
            "hide_details",
            "update_app",
            "cancel_operation",
            "cancelling",
            "cancel_unresponsive",
        }
        assert set(labels) == expected
        assert all(v.strip() for v in labels.values()), "renderers never invent or drop text"

    def test_checklist_rows_carry_non_color_symbols(self, config) -> None:
        report = self._report(("docker_running", "ok", "docker fine"), ("port_drift", "error", "drift"))
        rows = ui_model.doctor_checklist_rows(report)
        assert rows[0] == ("ok", "✓ docker fine")
        assert rows[1] == ("error", "✗ drift"), "state must be readable without color"

    def test_primary_problem_is_the_first_error_with_both_texts(self, config) -> None:
        report = self._report(
            ("config_identity", "info", "app"),
            ("docker_running", "error", "docker: down"),
            ("port_drift", "error", "drift"),
        )
        card = ui_model.primary_problem(config, report)
        assert card is not None and card["id"] == "docker_running"
        assert card["meaning"].strip() and card["fix"].strip(), "both explanation sections must be filled"
        assert card["title"] and card["meaning_label"] and card["fix_label"]

    def test_no_problem_card_when_green(self, config) -> None:
        report = self._report(("docker_running", "ok", "fine"))
        assert ui_model.primary_problem(config, report) is None

    def test_status_headline_never_relies_on_color_alone(self, config) -> None:
        severity, text = ui_model.status_headline(config, "running")
        assert severity == "ok" and text.startswith("✓")
        severity, text = ui_model.status_headline(config, "running", health_ok=False)
        assert severity == "error" and text.startswith("✗")
        severity, text = ui_model.status_headline(config, "stopped")
        assert severity == "info" and text.startswith("·")

    def test_clipboard_texts_route_through_the_reports(self, config, monkeypatch) -> None:
        from docker_app_launcher import doctor
        from docker_app_launcher.diagnostics_report import SupportBundle

        monkeypatch.setattr(doctor, "collect_doctor_report", lambda c: self._report(("docker_running", "ok", "fine")))
        assert "✓ fine" in ui_model.diagnosis_clipboard_text(config)
        monkeypatch.setattr(doctor, "collect_support_bundle", lambda c: SupportBundle(fields={"app": "X"}))
        text = ui_model.support_bundle_clipboard_text(config)
        assert text.startswith("docker-app-launcher support bundle"), "bundle stays contents-first from the GUI too"

    def test_assistant_elements_are_the_enforced_set(self) -> None:
        assert ui_model.ASSISTANT_ELEMENTS == (
            "status_headline",
            "doctor_checklist",
            "problem_card",
            "copy_diagnosis_button",
            "copy_support_bundle_button",
            "log_toggle",
            "update_button",
            "cancel_button",
        ), "changing the element set is an API decision - update every frontend and this pin together"
