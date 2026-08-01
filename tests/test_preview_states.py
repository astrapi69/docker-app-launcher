"""#115: the preview switch opens a named UI state - and proves its two rules.

The rules are the whole point of the switch, so they are MEASURED at the real
window, not asserted about the source:

* touches no Docker - ``actions.get_state`` is replaced by a landmine
* writes nothing - the config directory is compared file-by-file before/after

Both are the kind of promise that is easy to make and easy to break later by
an innocent-looking line in a constructor.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from docker_app_launcher import preview_states
from docker_app_launcher.__main__ import build_parser
from docker_app_launcher.config import LauncherConfig


def _display_available() -> bool:
    if os.environ.get("DISPLAY"):
        return True
    try:
        with socket.create_connection(("127.0.0.1", 6099), timeout=0.3):
            return True
    except OSError:
        return False


class TestStateTable:
    def test_ids_are_unique_and_listed(self) -> None:
        ids = [s.id for s in preview_states.PREVIEW_STATE_TABLE]
        assert len(ids) == len(set(ids)), f"duplicate preview state ids: {ids}"
        assert tuple(ids) == preview_states.PREVIEW_STATES

    def test_every_state_declares_its_fidelity_and_why(self) -> None:
        # An image labelled "this is what a failure looks like" must not
        # quietly be a drawing of one - so a state that is fed says so, and
        # says WHAT is fed.
        for state in preview_states.PREVIEW_STATE_TABLE:
            assert state.fidelity in (preview_states.REAL, preview_states.FED), state.id
            assert state.note.strip(), f"{state.id} does not say how it was produced"
            assert state.summary.strip(), f"{state.id} has no summary"

    def test_the_prompted_states_are_all_present(self) -> None:
        # The six states named in #115: the ones a user never gets to see by
        # accident, which is why they are the ones worth capturing.
        assert set(preview_states.PREVIEW_STATES) >= {
            "fresh",
            "busy_cancellable",
            "failure_problem_card",
            "guard_unavailable",
            "long_log",
            "small_window",
        }

    def test_description_covers_every_state(self) -> None:
        text = preview_states.describe_states()
        for state in preview_states.PREVIEW_STATE_TABLE:
            assert state.id in text and state.summary in text

    def test_note_names_the_state_and_fails_loudly_when_unknown(self) -> None:
        assert "fresh" in preview_states.state_note("fresh")
        with pytest.raises(KeyError):
            preview_states.state_note("no-such-state")


class TestTitleMarker:
    """A picture nobody can file is not evidence - so the state names itself."""

    def test_marker_numbers_the_state_within_the_list(self) -> None:
        total = len(preview_states.PREVIEW_STATES)
        for index, state_id in enumerate(preview_states.PREVIEW_STATES, start=1):
            assert preview_states.title_marker(state_id) == f"[{index}/{total}] {state_id}"

    def test_the_number_is_the_state_own_not_a_tour_counter(self) -> None:
        # Same number whether it is shown alone or as part of the tour: the
        # index lives in the state list, so the two cannot disagree.
        assert preview_states.title_marker("fresh").startswith("[1/")
        last = preview_states.PREVIEW_STATES[-1]
        assert preview_states.title_marker(last).startswith(f"[{len(preview_states.PREVIEW_STATES)}/")


class TestMakefileReadsTheOneList:
    def test_the_tour_target_hardcodes_no_state(self) -> None:
        # The tour asks Python for the states. A copy in the Makefile would go
        # stale the first time a state is added - and nobody would notice,
        # because a shorter tour still looks like a working tour.
        makefile = Path(__file__).resolve().parents[1] / "Makefile"
        text = makefile.read_text(encoding="utf-8")
        assert "preview-tour:" in text, "the tour target vanished"
        hardcoded = [s for s in preview_states.PREVIEW_STATES if f'"{s}"' in text or f"'{s}'" in text]
        assert not hardcoded, f"Makefile copies state ids instead of asking for them: {hardcoded}"
        assert "PREVIEW_STATES" in text, "the tour must read the one list"


class TestCliSurface:
    def test_choices_come_from_the_one_list(self) -> None:
        # One source for switch and screenshots (#116) - a second list would
        # drift the moment a state is added to only one of them.
        action = next(a for a in build_parser()._actions if a.dest == "preview")
        assert action.choices is not None
        assert tuple(action.choices) == preview_states.PREVIEW_STATES

    def test_an_unknown_state_is_refused(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--preview", "not-a-state"])

    def test_the_help_lists_the_states(self) -> None:
        help_text = build_parser().format_help()
        assert "--preview" in help_text


@pytest.mark.skipif(not _display_available(), reason="needs a display (xvfb) for the real window")
class TestRealWindowRules:
    """The two rules, measured at the real Tk window."""

    @pytest.fixture
    def cfg(self, tmp_path: Path) -> LauncherConfig:
        return LauncherConfig(
            app_name="Preview",
            locale="en",
            install_dir=str(tmp_path / "app"),
            config_dir=str(tmp_path / "cfg"),
            update_check_enabled=False,
            cleanup_on_start=False,
            single_instance=False,
        ).resolve()

    @staticmethod
    def _tree(root: Path) -> set[str]:
        return {str(p.relative_to(root)) for p in root.rglob("*")} if root.exists() else set()

    @pytest.mark.parametrize("state", preview_states.PREVIEW_STATES)
    def test_opens_without_docker_and_writes_nothing(self, cfg, state: str, tmp_path, monkeypatch) -> None:
        from docker_app_launcher import actions
        from docker_app_launcher.frontends import tk_window

        def landmine(*args: object, **kwargs: object) -> str:
            raise AssertionError(f"preview state {state!r} asked the Docker daemon")

        # The windows call actions.get_state on the FACADE module object, so
        # this one patch reaches every frontend - the only daemon-touching
        # call the window makes at startup.
        monkeypatch.setattr(actions, "get_state", landmine)

        config_dir = Path(cfg.config_dir)
        before = self._tree(config_dir)

        window = tk_window.LauncherApp(cfg, preview_state=state)
        try:
            window.update()
            title = window.title()
            assert title, "the window rendered no title"
            assert preview_states.title_marker(state) in title, (
                f"the window does not name the state it shows: {title!r}"
            )
        finally:
            window.destroy()

        after = self._tree(config_dir)
        assert after == before, f"preview state {state!r} wrote into the config dir: {sorted(after - before)}"

    def test_failure_state_shows_a_real_problem_card(self, cfg, monkeypatch) -> None:
        # The card must carry the SHIPPED meaning/fix texts, not an empty
        # frame: a preview of an empty card teaches the wrong thing.
        from docker_app_launcher import actions, ui_model
        from docker_app_launcher.frontends import tk_window

        monkeypatch.setattr(actions, "get_state", lambda *a, **k: "not_installed")
        window = tk_window.LauncherApp(cfg, preview_state="failure_problem_card")
        try:
            window.update()
            assert preview_states._FAILURE_CHECK_ID in ui_model.ERROR_CHECK_IDS
            assert str(window._problem_meaning.cget("text")).strip(), "meaning text empty"
            assert str(window._problem_fix.cget("text")).strip(), "fix text empty"
        finally:
            window.destroy()

    def test_busy_state_shows_the_cancel_control(self, cfg, monkeypatch) -> None:
        from docker_app_launcher import actions
        from docker_app_launcher.frontends import tk_window

        monkeypatch.setattr(actions, "get_state", lambda *a, **k: "not_installed")
        window = tk_window.LauncherApp(cfg, preview_state="busy_cancellable")
        try:
            window.update()
            assert window._cancel_btn.winfo_ismapped(), "the busy preview shows no way out"
            assert window._progress_frame.winfo_ismapped(), "no progress while an operation runs"
        finally:
            window.destroy()
