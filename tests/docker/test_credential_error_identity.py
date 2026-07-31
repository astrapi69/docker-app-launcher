"""The credential-helper classification, pinned against docker-py (#110).

``dockerfile_backend._classified_detail`` decides by the exception's CLASS
NAME string::

    if type(exc).__name__ == "StoreError":

The existing mocks define their OWN ``class StoreError(Exception)`` and
raise that, so they prove the branch fires for any class with that name -
and nothing about what the library actually raises. A rename or a move in
a docker-py upgrade would keep them green while the launcher silently
fell back to echoing a raw library error, which is exactly what #77
forbade.

This suite therefore asserts against the INSTALLED library, not against a
stand-in. It needs no daemon: the class identity is the assumption, and it
is what a dependency upgrade breaks.

MEASURED once by hand (docker-py 7.2.0, real daemon), and the reason the
wrapped path is pinned here too:

- ``client.api.build(...)`` with a broken ``credsStore`` raises
  ``docker.credentials.errors.StoreError`` UNWRAPPED - the launcher's path,
  and the classification matches.
- ``AuthConfig.resolve_authconfig(...)`` raises
  ``docker.errors.DockerException("Credentials store error: StoreError(...)")``
  - WRAPPED, where ``type(exc).__name__`` is ``DockerException`` and the
  classification would NOT match. Nothing in the launcher takes that path
  today; if something ever does, this test says what changes.
"""

from __future__ import annotations

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import dockerfile_backend


def _cfg(**kw: object) -> LauncherConfig:
    return LauncherConfig(
        app_name="Creds",
        container_name="creds",
        image_name="creds:test",
        compose_file="docker-compose.yml",
        locale="en",
        **kw,  # type: ignore[arg-type]
    )


class TestTheLibraryStillRaisesWhatTheClassifierMatches:
    def test_docker_py_exposes_store_error_under_that_exact_name(self) -> None:
        """The whole classification hangs on this name."""
        from docker.credentials.errors import StoreError

        assert StoreError.__name__ == "StoreError", (
            "docker-py renamed the credential-store exception - "
            "dockerfile_backend._classified_detail matches on type(exc).__name__ "
            "and would stop classifying it (#110)"
        )
        assert issubclass(StoreError, Exception)

    def test_the_real_exception_is_classified_not_echoed(self) -> None:
        """Raise the LIBRARY's exception, not a stand-in with the same name."""
        from docker.credentials.errors import StoreError

        exc = StoreError("docker-credential-gcloud not installed or not available in PATH")
        detail = dockerfile_backend._classified_detail(exc, _cfg())

        assert "credential" in detail.lower()
        assert detail != str(exc), "the raw library error must never be the whole message (#77)"

    def test_the_opt_in_variant_names_the_config_field(self) -> None:
        from docker.credentials.errors import StoreError

        detail = dockerfile_backend._classified_detail(StoreError("helper gone"), _cfg(use_registry_credentials=True))
        assert "use_registry_credentials" in detail

    def test_the_wrapped_variant_is_a_known_non_match(self) -> None:
        """Documents the measured second path, so a future change is not a surprise.

        ``AuthConfig.resolve_authconfig`` wraps StoreError in DockerException;
        the classifier matches on the class name and therefore does NOT fire.
        Pinned so that routing credential resolution through that path shows up
        here rather than as a mysteriously unhelpful message in the field.
        """
        from docker.errors import DockerException

        wrapped = DockerException("Credentials store error: StoreError('helper gone')")
        assert type(wrapped).__name__ != "StoreError"
        detail = dockerfile_backend._classified_detail(wrapped, _cfg())
        assert "use_registry_credentials" not in detail


class TestTheStandInMockWouldNotHaveCaught:
    """The RED proof for this suite: a look-alike class passes the old shape."""

    def test_a_look_alike_class_satisfies_the_classifier(self) -> None:
        class StoreError(Exception):
            pass

        detail = dockerfile_backend._classified_detail(StoreError("anything"), _cfg())
        assert "credential" in detail.lower(), (
            "this is what the previous tests proved - a class NAMED StoreError, "
            "not the library's. Hence the library-identity test above."
        )

    def test_a_renamed_library_class_would_slip_through_a_stand_in_mock(self) -> None:
        class CredentialStoreError(Exception):
            """What a docker-py rename would look like."""

        detail = dockerfile_backend._classified_detail(CredentialStoreError("helper gone"), _cfg())
        assert "use_registry_credentials" not in detail, (
            "a renamed exception falls through to the generic path - which is why "
            "the name is pinned against the installed library, not against a mock"
        )


@pytest.mark.parametrize("attr", ["credentials", "errors"])
def test_the_import_path_itself_is_part_of_the_contract(attr: str) -> None:
    """A move (not just a rename) breaks the classification just as quietly."""
    import docker

    assert hasattr(docker, attr), f"docker.{attr} disappeared - re-check the #110 assumption"
