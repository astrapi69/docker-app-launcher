"""Named UI states the window can be opened in for LOOKING at it (#115).

Everything else in this project checks that elements EXIST and what they
MEAN - element parity across the three frontends, per-check-id explanation
texts, end-state coverage, the frozen contract. None of it shows how the
window LOOKS. That is why the text-wrapping bug (#47) and the unresizable
window were found on a device rather than in CI.

This module is the SINGLE list of states, so the preview switch and the CI
screenshots (#116) cannot drift apart: both read ``PREVIEW_STATES``.

Two hard rules, because a preview must never be mistaken for the app:

* **It touches no Docker.** ``_refresh(state=...)`` takes the state as a
  value instead of asking the daemon - the only Docker call the window
  makes at startup.
* **It writes nothing.** No launcher.json, no ``.env``, no pending marker,
  no manifest line. Note the deliberate difference from ``--render-probe``,
  which ARMS the pending marker on purpose to prove the guard works at the
  built artifact's anchor: the preview does not, because it is a looking
  tool, not a proof.

Where a state cannot be produced for real without breaking one of those two
rules, it is marked ``fidelity="fed"`` and the note says what is fed. It is
never silently faked: an image labelled "this is what a failure looks like"
must not quietly be a drawing of one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from docker_app_launcher import i18n
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.diagnostics_report import CheckResult, DoctorReport

# How faithfully a state reproduces the real thing.
REAL = "real"  # produced end-to-end by the normal machinery
FED = "fed"  # real rendering chain, but its INPUT is supplied here


@dataclass(frozen=True)
class PreviewState:
    """One named state: what it shows, and how honestly it was produced."""

    id: str
    summary: str
    fidelity: str
    note: str


PREVIEW_STATE_TABLE: tuple[PreviewState, ...] = (
    PreviewState(
        "fresh",
        "the window right after start, nothing installed",
        FED,
        "the app state is passed as a value ('not_installed') instead of asked from the daemon; "
        "everything below that - headline, button states, focus, port field - is the real chain",
    ),
    PreviewState(
        "busy_cancellable",
        "a long-running operation with a visible Cancel control",
        REAL,
        "driven through the same _set_busy / _show_cancel_for / _update_progress the actions use",
    ),
    PreviewState(
        "failure_problem_card",
        "a failed check with the problem card (class, meaning, fix)",
        FED,
        "the DoctorReport is built here because a real one queries Docker; it then goes through the "
        "real _render_doctor -> ui_model.primary_problem -> _show_problem_card chain, so the card's "
        "texts are the shipped ones",
    ),
    PreviewState(
        "guard_unavailable",
        "the note that the concurrency guard cannot do its job",
        FED,
        "the real localized guard_unavailable text; producing it for real needs an unreadable marker "
        "path, which would mean writing - forbidden here",
    ),
    PreviewState(
        "long_log",
        "a long, wrapping text in the log panel",
        REAL,
        "real lines through the real _log; this is the state the #47 wrapping bug lived in",
    ),
    PreviewState(
        "small_window",
        "the window at its minimum size",
        REAL,
        "the real geometry path; what a small screen actually gets",
    ),
)

PREVIEW_STATES: tuple[str, ...] = tuple(s.id for s in PREVIEW_STATE_TABLE)

# The check id the failure preview renders. Deliberately one of
# ui_model.ERROR_CHECK_IDS, so the card shows the SHIPPED meaning/fix texts
# rather than an empty card - a preview of an empty card would teach the
# wrong thing about how a failure looks.
_FAILURE_CHECK_ID = "docker_running"

_LONG_LOG_LINES = 40


def describe_states() -> str:
    """The human-readable state list - one source for --help and the report."""
    width = max(len(s.id) for s in PREVIEW_STATE_TABLE)
    return "\n".join(f"  {s.id:<{width}}  {s.summary} [{s.fidelity}]" for s in PREVIEW_STATE_TABLE)


def state_note(state_id: str) -> str:
    """Why a state is real or fed - printed with the preview so the viewer
    always knows what they are looking at."""
    for state in PREVIEW_STATE_TABLE:
        if state.id == state_id:
            return f"{state.id} [{state.fidelity}]: {state.note}"
    raise KeyError(state_id)


def apply_preview_state(window: Any, state_id: str, config: LauncherConfig) -> None:
    """Put ``window`` into ``state_id``.

    Only the methods ALL THREE frontends share are used (measured, not
    assumed: _refresh, _set_busy, _show_cancel_for, _update_progress, _log,
    _render_doctor), so a preview state cannot exist for one window only -
    the same drift-proofing the assistant parity suite gives the elements.
    """
    if state_id not in PREVIEW_STATES:
        raise KeyError(f"unknown preview state {state_id!r} (known: {', '.join(PREVIEW_STATES)})")

    # Every state starts from a rendered window whose state came as a VALUE.
    window._refresh(state="not_installed")

    if state_id == "fresh":
        return

    if state_id == "busy_cancellable":
        window._set_busy(True)
        window._show_cancel_for("install")
        window._update_progress(35, i18n.t("building", config))
        window._log(i18n.t("install_needs_network", config))
        return

    if state_id == "failure_problem_card":
        window._render_doctor(_failure_report(config))
        return

    if state_id == "guard_unavailable":
        window._log(
            i18n.t("guard_unavailable", config, detail="preview: the marker file could not be read"),
            tag="err",
        )
        return

    if state_id == "long_log":
        for number in range(1, _LONG_LOG_LINES + 1):
            window._log(f"{number:02d} {i18n.t('install_needs_network', config)}")
        return

    if state_id == "small_window":
        _shrink(window, config)
        return


def _failure_report(config: LauncherConfig) -> DoctorReport:
    """A report with ONE error check, so the card renders class + meaning + fix."""
    mode = config.effective_deployment_mode
    return DoctorReport(
        app_name=config.app_name,
        mode=mode,
        checks=[
            CheckResult("config_identity", "info", f"app: {config.app_name} | mode: {mode}"),
            CheckResult(_FAILURE_CHECK_ID, "error", "docker: the daemon is not reachable"),
        ],
        complete=False,
    )


def _shrink(window: Any, config: LauncherConfig) -> None:
    """The smallest size the window itself permits - Tk-family and Qt differ
    in the call, so both spellings are tried before giving up quietly."""
    width = min(600, config.window_width)
    height = min(420, config.window_height)
    if hasattr(window, "geometry") and not hasattr(window, "resize"):
        window.geometry(f"{width}x{height}")
    elif hasattr(window, "resize"):
        window.resize(width, height)
