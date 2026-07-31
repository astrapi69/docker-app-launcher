"""The old-engine cell must say WHICH verdict it reached (#109).

The cell documented a classification - "flake = attempt 1 warns, attempt 2
green; real = both attempts down" - that measurably does NOT hold: the same
tree was red at 12:33 and green at 12:37, both attempts down each time, with
the dind entrypoint reporting ``sed: write error`` (a WRITE failure, not an
engine-generation fact).

Why that matters more than one red run: the job sits in the release-blocking
set (#93). A job that is intermittently red there teaches people to re-run
until green, and then the assurance is worth nothing without anyone
noticing. So the cell measures the runner's resources BEFORE the start and
names one of three verdicts, each with its own exit code.

The shell cannot be unit-tested against a real dind here, so this suite
pins the contract at source level - the established pattern in this repo for
shell gates (same as the guard's sync pins).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "integration" / "run_image_mode_old_engine_integration.sh"

VERDICTS = (
    "ENGINE-NEVER-STARTED / INFRASTRUCTURE",
    "ENGINE-NEVER-STARTED / UNDIAGNOSED",
    "FINDING",
    "PASS",
)


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


class TestTheCellNamesItsVerdict:
    def test_every_verdict_is_reachable_in_the_script(self, script_text: str) -> None:
        missing = [v for v in VERDICTS if f"VERDICT: {v}" not in script_text]
        assert not missing, f"checked {len(VERDICTS)} verdicts {list(VERDICTS)} - not emitted anywhere: {missing}"

    def test_infrastructure_and_finding_do_not_share_an_exit_code(self, script_text: str) -> None:
        """A caller must be able to tell 'measured nothing' from 'measured a gap'."""
        infra = script_text.index("VERDICT: ENGINE-NEVER-STARTED / INFRASTRUCTURE")
        assert "exit 2" in script_text[infra : infra + 500], "the infrastructure verdict must have its own exit code"

    def test_no_verdict_exits_zero(self, script_text: str) -> None:
        """'Could not check' must never read as 'nothing to find' (contract point 3)."""
        for verdict in ("INFRASTRUCTURE", "UNDIAGNOSED"):
            block_start = script_text.index(f"VERDICT: ENGINE-NEVER-STARTED / {verdict}")
            block = script_text[block_start : block_start + 500]
            assert "exit 0" not in block, f"the {verdict} verdict must not end green"


class TestResourcesAreMeasuredBeforeTheStart:
    def test_measurement_runs_before_the_engine_start(self, script_text: str) -> None:
        measure = script_text.index('report_resources "before start"')
        start = script_text.index("=== starting pinned old engine")
        assert measure < start, "the resource measurement must happen BEFORE the start, or it explains nothing"

    def test_disk_and_inodes_are_both_measured(self, script_text: str) -> None:
        assert "df -Pk" in script_text, "free space is not measured"
        assert "df -Pi" in script_text, "free inodes are not measured (a full inode table writes just as badly)"

    def test_thresholds_are_explicit_numbers(self, script_text: str) -> None:
        for name in ("MIN_FREE_KB", "MIN_FREE_INODES"):
            match = re.search(rf"^{name}=(\d+)", script_text, re.MULTILINE)
            assert match, f"{name} is not an explicit, readable threshold"
            assert int(match.group(1)) > 0

    def test_an_unmeasurable_basis_counts_as_exhausted(self, script_text: str) -> None:
        """Fail closed: no df output must not be read as 'plenty of room'."""
        fn_start = script_text.index("resources_exhausted()")
        fn = script_text[fn_start : script_text.index('report_resources "before start"')]
        assert '[ -z "$kb" ] && return 0' in fn, (
            "an empty measurement must classify as exhausted - claiming space we could "
            "not measure is the swallowed-probe class again"
        )


class TestTheStaleClassificationIsGone:
    def test_the_disproven_flake_rule_is_no_longer_claimed(self, script_text: str) -> None:
        """It said 'both attempts down = real'. The evidence in #109 disproves it."""
        assert "real = both attempts down" not in script_text, (
            "the script still documents the classification that #109 disproved"
        )


class TestScriptStaysExecutable:
    def test_shell_syntax_is_valid(self) -> None:
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_the_helpers_behave_as_documented(self) -> None:
        """Run the two helpers in isolation - they are the judge's own basis."""
        probe = f"""
        DOCKER_ROOT=/
        MIN_FREE_KB=1
        MIN_FREE_INODES=1
        {_extract_helpers(SCRIPT.read_text(encoding="utf-8"))}
        if resources_exhausted; then echo EXHAUSTED; else echo OK; fi
        DOCKER_ROOT=/definitely/not/a/mount/point
        if resources_exhausted; then echo CLOSED; else echo OPEN; fi
        """
        result = subprocess.run(["bash", "-c", probe], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.split()
        assert lines[0] == "OK", "a machine with space must not be called exhausted"
        assert lines[1] == "CLOSED", "an unmeasurable path must fail closed"


def _extract_helpers(text: str) -> str:
    start = text.index("free_kb()")
    end = text.index('report_resources "before start"')
    return text[start:end]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
