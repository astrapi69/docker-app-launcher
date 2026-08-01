"""#81: the check-id registry moved from the test suite into the package.

An interface consumers parse cannot live where shipped code is unable to
import it. Three proofs are required for the move, and they are the whole
point of this module - a registry that quietly gained or lost an id in
transit would change the API by accident, which is worse than leaving it in
the tests.

1. the source set is EXACTLY the set the tests carried,
2. the doctor emits EXACTLY that set, before and after,
3. the move is additive: nothing renamed, nothing removed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from docker_app_launcher import check_ids

# The set as it stood in tests/test_diagnostics_report.py before the move,
# written out INDEPENDENTLY here rather than imported. Double-entry
# bookkeeping: the source is the API, this literal is the witness. Importing
# the source would make the comparison tautological - it would prove only
# that a list equals itself.
_BASELINE_BEFORE_THE_MOVE = {
    "config_identity",
    "install_dir",
    "compose_file_exists",
    "image_source_declared",
    "dockerfile_exists",
    "docker_running",
    "toolchain_versions",
    "readiness",
    "readiness_blocker",
    "launcher_port",
    "state",
    "published_ports",
    "bind_address_open",
    "port_drift",
    "health_reachable",
    "last_operation_aborted",
}

_SRC = Path(__file__).resolve().parents[1] / "src" / "docker_app_launcher"


def _emitted_ids() -> set[str]:
    """Every id literal handed to a CheckResult anywhere in the package.

    Read from the SOURCE rather than from a list, because the behavioural
    truth is where the ids are constructed. Two files emit them: doctor.py
    (the real pass) and preview_states.py (the hand-fed failure preview) -
    the second one was found only by this sweep, and no list knew about it.
    """
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # The call is often split across lines, so match the id literal that
        # follows CheckResult( with any whitespace between.
        found.update(re.findall(r'CheckResult\(\s*"([a-z_]+)"', text))
        # preview_states passes the id through a module constant.
        for name in re.findall(r"CheckResult\(\s*(_[A-Z_]+)\s*,", text):
            constant = re.search(rf'^{name} = "([a-z_]+)"', text, re.M)
            if constant:
                found.add(constant.group(1))
    return found


class TestTheMoveChangedNothing:
    def test_the_source_set_equals_the_set_the_tests_carried(self) -> None:
        """Proof 1: identical across the move, measured not assumed."""
        moved = set(check_ids.KNOWN_CHECK_IDS)
        assert moved == _BASELINE_BEFORE_THE_MOVE, (
            f"the move changed the published set - added {sorted(moved - _BASELINE_BEFORE_THE_MOVE)}, "
            f"lost {sorted(_BASELINE_BEFORE_THE_MOVE - moved)}"
        )

    def test_the_doctor_emits_exactly_the_registered_set(self) -> None:
        """Proof 2: the registry describes behaviour, not a wish.

        A list that drifts from what the code emits is the state this move
        exists to end - four hand-maintained copies, none derived from the
        one that matters.
        """
        emitted = _emitted_ids()
        registered = set(check_ids.KNOWN_CHECK_IDS)
        assert emitted == registered, (
            f"registry and emitters disagree - emitted but unregistered {sorted(emitted - registered)}, "
            f"registered but never emitted {sorted(registered - emitted)}"
        )

    def test_no_duplicate_and_the_order_is_the_emission_order(self) -> None:
        assert len(check_ids.KNOWN_CHECK_IDS) == len(set(check_ids.KNOWN_CHECK_IDS))
        # config_identity is the first line of every pass; health_reachable
        # the last. Order is documentation, not enforcement.
        assert check_ids.KNOWN_CHECK_IDS[0] == "config_identity"
        assert check_ids.KNOWN_CHECK_IDS[-1] == "health_reachable"


class TestAdditiveOnly:
    def test_the_test_module_now_derives_instead_of_repeating(self) -> None:
        """Proof 3: the old carrier is a re-export, so it cannot drift.

        Leaving the literal in place would have kept a fifth copy alive - the
        exact thing being removed.
        """
        from tests.test_diagnostics_report import KNOWN_CHECK_IDS as legacy

        assert legacy == set(check_ids.KNOWN_CHECK_IDS)

    def test_membership_is_answerable_at_runtime(self) -> None:
        # The reason the registry had to be in the package at all: shipped
        # code can now check an id where it is emitted, not only where it is
        # tested.
        assert check_ids.is_known("docker_running")
        assert not check_ids.is_known("docker_runnning")


class TestSecondEmitterFileIsCovered:
    def test_the_preview_failure_id_is_registered(self) -> None:
        # Found by the sweep, not by any list: preview_states.py emits check
        # results too. A registry that missed it would have been incomplete
        # on its first day.
        from docker_app_launcher import preview_states

        assert check_ids.is_known(preview_states._FAILURE_CHECK_ID)
        assert check_ids.is_known("config_identity")


class TestTheGapWasClosedDeliberately:
    """The guard that used to hold this gap OPEN, rewritten rather than deleted.

    While the registry moved, this class asserted that error-capability was
    NOT answered here - so the convenient path (filling it in silently on the
    next touch) was blocked. #127 then decided it explicitly, and the decision
    was a concept and not a line: a warning is a state the user must know
    about, not a lesser error. The guard now records that the change happened
    on purpose, which is the only honest successor to a guard that has served.
    """

    def test_explanation_need_is_derived_from_emitted_statuses(self) -> None:
        derived = tuple(
            check_id
            for check_id in check_ids.KNOWN_CHECK_IDS
            if check_ids.CHECK_STATUSES[check_id] & check_ids.EXPLAINABLE_STATUSES
        )
        assert derived == check_ids.NEEDS_EXPLANATION_IDS

    def test_a_warning_needs_explaining_too(self) -> None:
        # The case that motivated it: 22 texts that could never be shown.
        assert "bind_address_open" in check_ids.NEEDS_EXPLANATION_IDS
        assert check_ids.CHECK_STATUSES["bind_address_open"] == frozenset({"warn"})

    def test_the_reason_stays_in_the_module(self) -> None:
        assert "DERIVED here now (#127)" in check_ids.__doc__


@pytest.mark.parametrize("check_id", check_ids.KNOWN_CHECK_IDS)
def test_every_registered_id_is_actually_emitted(check_id: str) -> None:
    """Per id, so a failure names WHICH one drifted rather than a set diff."""
    assert check_id in _emitted_ids(), f"{check_id} is registered but no longer emitted anywhere in src/"


class TestStatusesComeFromBehaviour:
    """#127: the declared status set is pinned against the emitting source.

    Declared in the package because a frozen bundle cannot parse its own
    source - so the declaration is the API and this is the witness, the same
    double-entry shape the registry itself uses.
    """

    @staticmethod
    def _emitted_statuses() -> dict[str, set[str]]:
        import re
        from collections import defaultdict

        found: dict[str, set[str]] = defaultdict(set)
        for path in _SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'CheckResult\(\s*"([a-z_]+)",\s*([^,]+),', text):
                found[match.group(1)].update(re.findall(r'"(ok|error|warn|info)"', match.group(2)))
            for match in re.finditer(r'CheckResult\(\s*(_[A-Z_]+),\s*("[a-z]+")', text):
                constant = re.search(rf'^{match.group(1)} = "([a-z_]+)"', text, re.M)
                if constant:
                    found[constant.group(1)].add(match.group(2).strip('"'))
        return found

    def test_every_declared_status_set_matches_the_source(self) -> None:
        emitted = self._emitted_statuses()
        for check_id, declared in check_ids.CHECK_STATUSES.items():
            assert set(declared) == emitted[check_id], (
                f"{check_id}: declared {sorted(declared)}, source emits {sorted(emitted[check_id])}"
            )

    def test_the_declaration_covers_every_registered_id(self) -> None:
        assert set(check_ids.CHECK_STATUSES) == set(check_ids.KNOWN_CHECK_IDS)

    def test_warn_is_a_status_of_its_own(self) -> None:
        # The whole concept fix: a warning is a state the user must know
        # about, not a lesser error. bind_address_open is the only emitter of
        # it, so this pins the case that motivated the change.
        assert check_ids.CHECK_STATUSES["bind_address_open"] == frozenset({"warn"})
        assert "warn" in check_ids.EXPLAINABLE_STATUSES
