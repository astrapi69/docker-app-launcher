"""ONE classifier for the action path, per class and per mode (#128).

Two defects this replaces, both measured:

**The class died at the boundary.** ``install``/``start``/``update`` return
``(ok, message)``. The classification that produced the message - StoreError,
socket permission, multi-arch gap, registry refusal - was flattened into prose
and lost. The assistant could therefore explain only what the DOCTOR found,
never what an action hit, because there was nothing to key an explanation to.

**The same cause classified differently per mode.** In image mode
``_classify_pull_error`` never routed through the exception classifier, so a
broken credential helper and a denied docker socket were literally unreachable
there - the two classes that cost the most time to diagnose. Nobody decided
that; it grew. Both backends now call :func:`classify` and get the same answer
for the same cause.

**And the text was English.** The classification layer produced hardcoded
English while the gate layer above it was translated, so a German user hit
English prose in the ``{detail}`` slot of a localized shell - exactly when it
helps least. Each class now carries an ``error_<id>`` key in all 11 catalogs.

A class that does NOT match returns :data:`~docker_app_launcher.check_ids.UNCLASSIFIED`
and the raw library line, deliberately: an unnamed cause must look unnamed, so
it reads as a gap to close rather than as a class that happens to be terse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from docker_app_launcher import check_ids, i18n
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import py_client

logger = logging.getLogger("docker_app_launcher.docker.error_classes")

# Registry answers that mean "the image is there, your architecture is not".
_PLATFORM_MARKERS = ("no matching manifest", "does not match the specified platform")

# The registry could not be reached at all.
_UNREACHABLE_MARKERS = ("no such host", "temporary failure", "timeout", "connection refused", "network is unreachable")

# The token flow answered, and said no. GHCR uses the same wording for a
# missing repository and a private one, which is why the text names both (#87).
_REFUSAL_MARKERS = (
    "denied",
    "unauthorized",
    "authentication required",
    "pull access denied",
    "repository does not exist",
)


@dataclass(frozen=True)
class ClassifiedError:
    """A named cause plus the text to show.

    ``id`` is empty exactly when nothing matched - see
    :data:`~docker_app_launcher.check_ids.UNCLASSIFIED`.
    """

    id: str
    detail: str

    @property
    def is_classified(self) -> bool:
        return bool(self.id)


def _text(config: LauncherConfig | None, class_id: str, **kwargs: object) -> str:
    """The localized message for a class. Falls back to English via i18n."""
    if config is None:
        # Some call sites have no config (a classifier reached from a helper
        # that never took one). English fallback beats losing the class.
        return i18n.STRINGS["en"].get(f"error_{class_id}", class_id).format(**kwargs)
    return i18n.t(f"error_{class_id}", config, **kwargs)


def classify_exception(exc: BaseException, config: LauncherConfig | None = None) -> ClassifiedError:
    """Classify a docker-py / OS exception into a named class.

    Called from BOTH backends now. Matching StoreError on the class NAME is
    deliberate and pinned by tests/docker/test_credential_error_identity.py
    against the real library: the exception is raised deep inside docker-py's
    auth resolution and importing it here would tie this module to a private
    path.
    """
    if type(exc).__name__ == "StoreError":
        class_id = (
            "credential_helper_broken_required"
            if config is not None and config.use_registry_credentials
            else "credential_helper_broken"
        )
        return ClassifiedError(class_id, _text(config, class_id, error=exc))
    if py_client._classify_exception(exc) == "permission":
        return ClassifiedError("docker_permission_denied", _text(config, "docker_permission_denied"))
    return ClassifiedError(check_ids.UNCLASSIFIED, str(exc))


def classify_message(message: str, config: LauncherConfig | None = None) -> ClassifiedError:
    """Classify an engine/registry message (pull stream, build stream)."""
    lower = (message or "").lower()
    reference = config.image_reference if config is not None else ""
    if any(m in lower for m in _PLATFORM_MARKERS):
        return ClassifiedError(
            "image_platform_missing", _text(config, "image_platform_missing", reference=reference, detail=message)
        )
    if any(m in lower for m in _UNREACHABLE_MARKERS):
        return ClassifiedError(
            "registry_unreachable", _text(config, "registry_unreachable", reference=reference, detail=message)
        )
    if any(m in lower for m in _REFUSAL_MARKERS):
        return ClassifiedError(
            "registry_refused", _text(config, "registry_refused", reference=reference, detail=message)
        )
    return ClassifiedError(check_ids.UNCLASSIFIED, message)


def classify(cause: BaseException | str, config: LauncherConfig | None = None) -> ClassifiedError:
    """The ONE entry point: an exception or a message, same vocabulary.

    An exception is tried as an exception FIRST and then as its own text, so a
    StoreError surfacing through a pull stream lands in the same class as one
    raised during a build. That ordering is the mode difference disappearing:
    before, whether a cause was recognised depended on which backend caught it.
    """
    if isinstance(cause, BaseException):
        classified = classify_exception(cause, config)
        if classified.is_classified:
            return classified
        return classify_message(str(cause), config)
    return classify_message(cause, config)


def detail_of(cause: BaseException | str, config: LauncherConfig | None = None) -> str:
    """Just the text - for the ``(ok, message)`` call sites that cannot carry
    an id yet. The class is still LOGGED, so it is visible in a bug report even
    where the return type cannot express it."""
    classified = classify(cause, config)
    if classified.is_classified:
        logger.info("classified action error: %s", classified.id)
    else:
        logger.info("unclassified action error - shown as the raw library line: %s", classified.detail[:200])
    return classified.detail
