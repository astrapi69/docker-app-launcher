"""User-doc coverage over the enumerable capability sets (#106).

Three doc gaps in one week (cancel, concurrency guard, stale feature
list) were found by a QUESTION, not a mechanism. This suite is the
mechanism: every entry of the sets the code already enumerates - the
CLI option set (live from ``build_parser``), the assistant element set
that already enforces three-frontend parity, and the guard's
user-visible note keys - must have a mention in the user docs
(``README.md`` and, for GUI capabilities, ``docs/quickstart-end-user.md``).

Contract properties:
- Fails CLOSED: a new CLI flag or assistant element without a doc
  mention (or an individually reasoned exception) is RED by default;
  a missing doc file raises instead of skipping.
- Reports WHAT it measured: assertion messages carry the count and the
  names of the checked entries.
- Matching normalizes whitespace before comparing, because doc prose
  wraps lines mid-phrase (measured: "concurrency guard cannot work"
  had zero naive-grep hits purely due to a line break).

Known limit, deliberately open: this suite proves a mention EXISTS,
not that it is still CORRECT - when a capability's behavior changes,
a stale mention stays green. A content hash over prose (the fix used
for the gate-rule coupling) would be wrong here: it fires on every
rewording and creates exactly the false alarms this check avoids.
Currency of the text stays with the release-checklist line and review.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from docker_app_launcher import ui_model
from docker_app_launcher.__main__ import build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
QUICKSTART = REPO_ROOT / "docs" / "quickstart-end-user.md"

# CLI flags that legitimately have no user-doc mention. Exceptions are
# INDIVIDUAL and each carries its reason - no namespace or file-level
# blanket exclusions.
INTERNAL_CLI_FLAGS: dict[str, str] = {
    "--help": "argparse built-in; self-documenting via --help itself",
    "--render-probe": "CI frozen-contract probe, not a user command",
}

# Assistant elements -> phrases the user docs must contain (any one of
# them, in README or quickstart). Internal element ids rarely match doc
# prose verbatim, so the mapping is declared here per element; an element
# missing from this table fails the suite (fail closed), same as an
# entry for an element that no longer exists (stale mapping).
ELEMENT_DOC_PATTERNS: dict[str, tuple[str, ...]] = {
    "status_headline": ("status line",),
    "doctor_checklist": ("check system",),
    "problem_card": ("problem card", "what does this mean"),
    "copy_diagnosis_button": ("copy diagnosis",),
    "copy_support_bundle_button": ("support bundle",),
    "log_toggle": ("collapsed", "details log"),
    "update_button": ("update",),
    "cancel_button": ("cancel",),
}

# Guard note keys -> phrases the user docs must contain (same rule).
GUARD_NOTE_DOC_PATTERNS: dict[str, tuple[str, ...]] = {
    "guard_unavailable": ("concurrency guard cannot work",),
    "pending_expired_unconfirmed": ("never confirmed its end",),
    "operation_pending_blocked": ("two operations",),
}


def _normalized(text: str) -> str:
    """Collapse all whitespace: doc prose wraps lines mid-phrase."""
    return re.sub(r"\s+", " ", text).lower()


def _user_cli_flags() -> list[str]:
    parser = build_parser()
    flags = sorted({s for a in parser._actions for s in a.option_strings if s.startswith("--")})
    return [f for f in flags if f not in INTERNAL_CLI_FLAGS]


def missing_cli_flags(readme_text: str) -> tuple[list[str], list[str]]:
    """(missing, measured) - every non-internal flag must appear literally in README."""
    norm = _normalized(readme_text)
    measured = _user_cli_flags()
    return [f for f in measured if f not in norm], measured


def missing_patterns(
    table: dict[str, tuple[str, ...]], readme_text: str, quickstart_text: str
) -> tuple[list[str], list[str]]:
    """(missing, measured) - each entry needs one of its phrases in either user doc."""
    corpus = _normalized(readme_text) + " " + _normalized(quickstart_text)
    measured = sorted(table)
    return [key for key in measured if not any(p in corpus for p in table[key])], measured


def _read(path: Path) -> str:
    # Deliberately no existence check: a missing user-doc file must raise
    # (fail closed), never let the suite pass vacuously.
    return path.read_text(encoding="utf-8")


class TestSetClassificationComplete:
    """Fail closed: unclassified entries are RED before any doc is read."""

    def test_every_assistant_element_has_a_doc_pattern(self) -> None:
        elements = set(ui_model.ASSISTANT_ELEMENTS)
        classified = set(ELEMENT_DOC_PATTERNS)
        assert elements == classified, (
            f"assistant elements and doc-pattern table diverge - "
            f"unclassified: {sorted(elements - classified)}, stale: {sorted(classified - elements)}"
        )

    def test_every_guard_note_key_has_a_doc_pattern(self) -> None:
        keys = set(ui_model.GUARD_USER_NOTE_KEYS)
        classified = set(GUARD_NOTE_DOC_PATTERNS)
        assert keys == classified, (
            f"guard note keys and doc-pattern table diverge - "
            f"unclassified: {sorted(keys - classified)}, stale: {sorted(classified - keys)}"
        )

    def test_guard_note_keys_match_the_gate_source(self) -> None:
        """Sync pin: the tuple must list exactly the i18n keys the gate uses."""
        source = inspect.getsource(ui_model.check_pending_operation)
        for key in ui_model.GUARD_USER_NOTE_KEYS:
            assert f'"{key}"' in source, (
                f"GUARD_USER_NOTE_KEYS lists '{key}' but check_pending_operation never emits it"
            )

    def test_internal_flag_exceptions_are_real_flags(self) -> None:
        """A stale exception (flag removed/renamed) must not linger."""
        parser = build_parser()
        all_flags = {s for a in parser._actions for s in a.option_strings if s.startswith("--")}
        stale = set(INTERNAL_CLI_FLAGS) - all_flags
        assert not stale, f"INTERNAL_CLI_FLAGS lists flags that no longer exist: {sorted(stale)}"


class TestUserDocsCoverTheEnumerableSets:
    def test_every_user_cli_flag_is_documented_in_readme(self) -> None:
        missing, measured = missing_cli_flags(_read(README))
        assert not missing, (
            f"checked {len(measured)} CLI flags ({', '.join(measured)}) against README.md - "
            f"undocumented: {missing}. Document the flag or add an individually "
            f"reasoned entry to INTERNAL_CLI_FLAGS."
        )

    def test_every_assistant_element_is_mentioned_in_user_docs(self) -> None:
        missing, measured = missing_patterns(ELEMENT_DOC_PATTERNS, _read(README), _read(QUICKSTART))
        assert not missing, (
            f"checked {len(measured)} assistant elements ({', '.join(measured)}) against "
            f"README.md + quickstart - no doc mention for: {missing}"
        )

    def test_every_guard_note_is_mentioned_in_user_docs(self) -> None:
        missing, measured = missing_patterns(GUARD_NOTE_DOC_PATTERNS, _read(README), _read(QUICKSTART))
        assert not missing, (
            f"checked {len(measured)} guard notes ({', '.join(measured)}) against "
            f"README.md + quickstart - no doc mention for: {missing}"
        )

    def test_measured_set_sizes_are_pinned(self) -> None:
        """The check must SHRINK visibly if an enumeration source dries up."""
        flags = _user_cli_flags()
        assert len(flags) >= 19, f"CLI flag enumeration collapsed: only {flags}"
        assert len(ui_model.ASSISTANT_ELEMENTS) >= 8
        assert len(ui_model.GUARD_USER_NOTE_KEYS) >= 3


class TestJudgeSelfTest:
    """The checker itself must be able to go RED (five-point contract, point 1)."""

    def test_empty_docs_fail_every_entry(self) -> None:
        missing, measured = missing_cli_flags("")
        assert missing == measured and measured
        missing2, measured2 = missing_patterns(ELEMENT_DOC_PATTERNS, "", "")
        assert missing2 == measured2 and measured2

    def test_line_wrapped_phrase_is_still_found(self) -> None:
        """Regression for the measured false negative: phrase split by a newline."""
        wrapped = 'the "concurrency guard\ncannot work" note'
        missing, _ = missing_patterns({"guard_unavailable": ("concurrency guard cannot work",)}, wrapped, "")
        assert missing == []

    def test_missing_doc_file_raises_instead_of_passing(self) -> None:
        with pytest.raises(FileNotFoundError):
            _read(REPO_ROOT / "does-not-exist.md")
