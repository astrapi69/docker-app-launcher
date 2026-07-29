"""Structured diagnosis reports: built once, rendered everywhere (#86).

The CLI renders these as text or ``--json``; the GUI (#81) renders the SAME
objects as status header, checklists and problem cards. That is the parity
contract: no GUI function without an actions core, no CLI function without a
testable report object.

``id`` fields are API: stable across releases, additive evolution only.
Report labels are technical English on purpose (diagnostic artifacts pasted
into bug reports); localized texts enter through the messages the gates and
lifecycle already produce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Statuses a CheckResult can carry. "info" lines never count as problems.
_STATUSES = ("ok", "warn", "error", "info")

# Env keys whose NAME already suggests a secret: the support bundle lists
# key names only (never values), and these names are withheld entirely.
_SECRET_KEY_PATTERN = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL|API", re.IGNORECASE)


@dataclass
class CheckResult:
    """One verifiable finding: stable ``id``, status, message, optional fix."""

    id: str
    status: str
    message: str
    fix: str = ""

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"status must be one of {_STATUSES}, got {self.status!r}")

    def to_dict(self) -> dict[str, str]:
        data = {"id": self.id, "status": self.status, "message": self.message}
        if self.fix:
            data["fix"] = self.fix
        return data


@dataclass
class DoctorReport:
    """The full diagnostic pass as data; the text report renders FROM this."""

    app_name: str
    mode: str
    checks: list[CheckResult] = field(default_factory=list)
    # False when the pass stopped early (daemon down): later checks were
    # never probed, so their absence is not their success.
    complete: bool = True

    @property
    def problems(self) -> int:
        return sum(1 for c in self.checks if c.status == "error")

    @property
    def ok(self) -> bool:
        return self.problems == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app_name,
            "deployment_mode": self.mode,
            "ok": self.ok and self.complete,
            "complete": self.complete,
            "problems": self.problems,
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class StatusReport:
    """State + port + health in one object - '--status' stops hiding drift."""

    app_name: str
    mode: str
    state: str
    port: int
    url: str
    health_ok: bool | None = None  # None: not probed (stack not running)
    health_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app_name,
            "deployment_mode": self.mode,
            "state": self.state,
            "port": self.port,
            "url": self.url,
            "health": None if self.health_ok is None else {"ok": self.health_ok, "detail": self.health_detail},
        }

    def to_text(self) -> str:
        line = f"Status: {self.state}"
        if self.health_ok is False:
            # The running-but-broken case must never read as a plain "running".
            line += f" (but health FAILED on {self.url}: {self.health_detail})"
        elif self.health_ok is True:
            line += f" (healthy: {self.url})"
        return line


@dataclass
class HealthReport:
    ok: bool
    detail: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail, "url": self.url}


@dataclass
class SupportBundle:
    """A HUMAN-READABLE diagnosis document - never an opaque archive.

    The user can (and should) read it before sending; ``to_text`` states
    FIRST what the bundle contains. No env values, no tokens, no
    credentials - env key NAMES only, and names that themselves look like
    secrets are withheld (:data:`_SECRET_KEY_PATTERN`).
    """

    fields: dict[str, Any] = field(default_factory=dict)

    CONTENTS = (
        "launcher/app identity, deployment mode, OS, Docker toolchain "
        "versions, install state, port, health, image identity (from the "
        "install manifest), config paths, env KEY NAMES. It contains NO "
        "environment values, NO tokens, NO credentials."
    )

    def to_dict(self) -> dict[str, Any]:
        return {"contains": self.CONTENTS, **self.fields}

    def to_text(self) -> str:
        lines = ["docker-app-launcher support bundle", f"contains: {self.CONTENTS}", ""]
        lines.extend(f"{key}: {value}" for key, value in self.fields.items())
        return "\n".join(lines)


def visible_env_keys(container_env: dict[str, str]) -> list[str]:
    """Env key NAMES safe to list in a bundle - secret-looking names withheld."""
    return [k for k in container_env if not _SECRET_KEY_PATTERN.search(k)]
