"""The diagnostic check-id vocabulary - in the SHIPPED package (#81).

These ids are documented as an API: ``--doctor --json`` emits them, the
README points consumers at them, and the project promises additive evolution
only. Until this module existed, the only COMPLETE enumeration of them lived
in ``tests/test_diagnostics_report.py`` - a test module, which shipped code
cannot import. So the runtime could not validate an id against the very list
it publishes, and anyone building on the API was pointed at a registry that
is not in the distribution.

Measured before the move (three independent sweeps, each adversarially
re-checked): 16 distinct ids across 18 emission sites in ``doctor.py``, plus
2 more sites in ``preview_states.py`` that no list knew about. The set here is
EXACTLY the set the tests carried - proven by
``tests/test_check_ids.py::TestTheMoveChangedNothing``, which compares this
module against an independently written baseline literal and against what the
doctor really emits.

Deliberately NOT here: which ids are error-capable. That question is answered
today by set arithmetic over two hand-maintained literals which never read a
status the doctor actually emits - and it answers wrongly (#127: the security
warning is the only ``warn`` emitter in the project, is listed as
error-capable, and its explanation card can therefore never render). Deriving
it belongs with that fix, not with a move that must change nothing.
"""

from __future__ import annotations

#: Every check id the package can emit, in the order a full doctor pass
#: produces them. ADDITIVE EVOLUTION ONLY: an id may be added, never renamed
#: or removed - consumers parse these.
KNOWN_CHECK_IDS: tuple[str, ...] = (
    "config_identity",
    "install_dir",
    "compose_file_exists",
    "image_source_declared",
    "dockerfile_exists",
    "docker_running",
    "toolchain_versions",
    "readiness_blocker",
    "readiness",
    "launcher_port",
    "state",
    "last_operation_aborted",
    "published_ports",
    "bind_address_open",
    "port_drift",
    "health_reachable",
)

#: Membership test for the runtime. The point of having the registry in the
#: package at all: an id can be checked where it is emitted, not only where
#: it is tested.
KNOWN_CHECK_ID_SET: frozenset[str] = frozenset(KNOWN_CHECK_IDS)


def is_known(check_id: str) -> bool:
    """Whether ``check_id`` belongs to the published vocabulary."""
    return check_id in KNOWN_CHECK_ID_SET
