"""#116: the preview states join the screenshot set the run already uploads.

This repository ALREADY captures window screenshots and attaches them to every
CI run (``DAL_SCREENSHOTS=1``, ``make screenshots``, artifact
``gui-screenshots`` - 549 KB of real images in the run this was written
against). Building a second capture mechanism next to it would have been the
exact drift the parity suites exist to prevent: two tool chains, two output
directories, two artifacts, and nobody sure which one to look at.

What was actually missing is measured, not assumed. Of the six states #116
names - the ones a user never sees by accident - the existing 29 screenshots
covered exactly ONE (`not_installed`, i.e. `fresh`). Cancel-while-busy, the
problem card, the guard note, a long wrapping log and the minimum window size
had no picture at all.

So this module adds those states to the SAME mechanism: same helper, same
directory, same artifact. It also writes MANIFEST.md, because a picture whose
state is real and one whose input was fed must not look alike in a folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docker_app_launcher import preview_states
from docker_app_launcher.config import LauncherConfig
from tests.test_gui_qt import _qt_screenshot
from tests.test_gui_window import SCREENSHOT_DIR, _display_available, _screenshot

pytestmark = pytest.mark.skipif(not _display_available(), reason="no display (run under xvfb-run)")

_FRONTENDS = [
    ("tk", "tk_window", "LauncherApp", None),
    ("ctk", "ctk_window", "CtkLauncherApp", "HAS_CTK"),
    ("qt", "qt_window", "QtLauncherApp", "HAS_QT"),
]


def _config(tmp_path: Path, backend: str) -> LauncherConfig:
    return LauncherConfig(
        app_name="Preview",
        gui_backend=backend,
        locale="en",
        install_dir=str(tmp_path / "app"),
        config_dir=str(tmp_path / "cfg"),
        update_check_enabled=False,
        cleanup_on_start=False,
        single_instance=False,
    ).resolve()


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch):
    """The preview never asks Docker - pinned here too, so a screenshot run
    cannot become the one path that quietly does."""
    from docker_app_launcher import actions

    monkeypatch.setattr(actions, "get_state", lambda *a, **k: "not_installed")


@pytest.mark.parametrize("state", preview_states.PREVIEW_STATES)
@pytest.mark.parametrize(("backend", "module_name", "class_name", "guard"), _FRONTENDS)
def test_capture_preview_state(tmp_path, state, backend, module_name, class_name, guard) -> None:
    """Open one preview state and photograph it - for a human, not for a gate."""
    import importlib

    module = importlib.import_module(f"docker_app_launcher.frontends.{module_name}")
    if guard is not None and not getattr(module, guard):
        pytest.skip(f"{backend}: toolkit not installed")
    if backend == "qt":
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

    window = getattr(module, class_name)(_config(tmp_path, backend), preview_state=state)
    try:
        # The assertion is the cheap one that must hold anyway; the picture is
        # the point, and a missing picture is a warning, never a failure (#116).
        if hasattr(window, "update"):
            window.update()
        index = preview_states.PREVIEW_STATES.index(state) + 1
        name = f"preview_{backend}_{index}_{state}"
        # Two shooters, because the toolkits genuinely differ: the Tk-family
        # helper grabs the screen region under the window, Qt renders itself
        # via QWidget.grab(). Both already exist here - using one for both
        # would fail for exactly one frontend, which is how a screenshot set
        # ends up silently covering two thirds.
        (_qt_screenshot if backend == "qt" else _screenshot)(window, name)
        assert window is not None
    finally:
        (window.destroy if hasattr(window, "destroy") else window.close)()


def test_manifest_says_what_each_picture_shows() -> None:
    """Write MANIFEST.md next to the images.

    Without it a folder of PNGs cannot answer the one question that decides
    whether a picture is evidence: was this state produced for real, or was its
    input supplied because producing it for real would need Docker or a write?
    """
    if not os.environ.get("DAL_SCREENSHOTS"):
        pytest.skip("screenshots not requested (set DAL_SCREENSHOTS=1)")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Preview-state screenshots (#116)",
        "",
        "One picture per UI state per frontend, named `preview_<frontend>_<n>_<state>.png`.",
        "The window title in the picture carries the same `[n/N] <state>` marker, so a",
        "screenshot pulled out of this folder still names what it shows.",
        "",
        "For people to LOOK at. No automatic comparison, no gate: three toolkits with",
        "different font rendering produce more false alarms than insight.",
        "",
        "## Real or fed",
        "",
        "A `[fed]` state runs the shipped rendering chain with its input supplied,",
        "because producing it for real would need Docker or a write. Do not read a",
        "`[fed]` picture as proof that the real path produces it.",
        "",
        *(f"- {preview_states.state_note(state)}" for state in preview_states.PREVIEW_STATES),
        "",
    ]
    (SCREENSHOT_DIR / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")
    assert (SCREENSHOT_DIR / "MANIFEST.md").is_file()
