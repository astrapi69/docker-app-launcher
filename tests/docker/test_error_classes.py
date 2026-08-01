"""#128: one classifier for the action path - same cause, same class, any mode.

Three properties, each of which was measurably false before:

1. an action failure carries a CLASS, not just prose;
2. the class does not depend on which backend caught the cause;
3. the text is localized, in a project that enforces 11-language parity
   everywhere else.
"""

from __future__ import annotations

import pytest

from docker_app_launcher import check_ids, i18n
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker import dockerfile_backend, error_classes, image_backend


class StoreError(Exception):
    """Stands in for docker.credentials.errors.StoreError.

    NAMED exactly StoreError on purpose: production matches on the class name,
    so a stand-in called anything else would classify as something entirely
    different - which is what the first run of this suite proved by returning
    registry_refused for a helper failure.

    The production match is on the class NAME, and that assumption is pinned
    against the REAL library in tests/docker/test_credential_error_identity.py
    (#110) - so this stand-in cannot drift into proving only its own shape.
    """


@pytest.fixture
def cfg(tmp_path):
    return LauncherConfig(
        app_name="Classes",
        locale="en",
        deployment_mode="image",
        image_reference="ghcr.io/owner/app:1.0.0",
        install_dir=str(tmp_path),
    ).resolve()


class TestTheModeDifferenceIsGone:
    """The heart of #128.

    Before: image mode's _classify_pull_error never consulted the exception
    classifier, so a broken credential helper and a denied docker socket - the
    two costliest classes to diagnose - were literally unreachable there. The
    same cause produced a different message depending on deployment mode, and
    nobody had decided that.
    """

    @pytest.mark.parametrize(
        ("cause", "expected"),
        [
            (StoreError("no such helper"), "credential_helper_broken"),
            (PermissionError(13, "Permission denied"), "docker_permission_denied"),
        ],
    )
    def test_both_backends_answer_identically(self, cfg, cause, expected) -> None:
        assert error_classes.classify(cause, cfg).id == expected
        assert dockerfile_backend._classified_detail(cause, cfg) == image_backend._classify_pull_error(cause, cfg)

    def test_the_credential_class_is_reachable_in_image_mode(self, cfg) -> None:
        # The specific regression: this used to fall through to raw message
        # matching and surface as a library line.
        detail = image_backend._classify_pull_error(StoreError("gcloud not found"), cfg)
        assert "credsStore" in detail or "credHelpers" in detail

    def test_a_declared_credential_config_gets_the_OTHER_remedy(self, cfg) -> None:
        # Same breakage, opposite instruction: repair the helper rather than
        # remove the entry. Two classes because the fix genuinely differs.
        cfg.use_registry_credentials = True
        assert error_classes.classify(StoreError("boom"), cfg).id == "credential_helper_broken_required"


class TestClassesAreRegistered:
    def test_every_produced_class_is_in_the_registry(self, cfg) -> None:
        produced = {
            error_classes.classify(StoreError("x"), cfg).id,
            error_classes.classify(PermissionError(13, "denied"), cfg).id,
            error_classes.classify("no matching manifest for linux/arm64", cfg).id,
            error_classes.classify("dial tcp: no such host", cfg).id,
            error_classes.classify("pull access denied", cfg).id,
        }
        unregistered = produced - set(check_ids.ACTION_ERROR_IDS)
        assert not unregistered, f"classes produced but not registered: {sorted(unregistered)}"

    def test_every_registered_class_has_its_text_in_all_eleven_catalogs(self) -> None:
        missing: list[str] = []
        for class_id in check_ids.ACTION_ERROR_IDS:
            for code in i18n.available_languages():
                if not str(i18n.STRINGS[code].get(f"error_{class_id}", "")).strip():
                    missing.append(f"{code}:error_{class_id}")
        assert not missing, f"action classes without text: {missing}"

    def test_action_ids_are_kept_out_of_the_doctor_vocabulary(self) -> None:
        # Folding them into KNOWN_CHECK_IDS would break the registry's proof
        # that it equals what CheckResult actually carries (#81).
        assert not set(check_ids.ACTION_ERROR_IDS) & set(check_ids.KNOWN_CHECK_IDS)
        assert set(check_ids.ALL_PROBLEM_IDS) == set(check_ids.KNOWN_CHECK_IDS) | set(check_ids.ACTION_ERROR_IDS)


class TestTextIsLocalized:
    @pytest.mark.parametrize("locale", ["de", "ja", "tr"])
    def test_the_detail_is_not_english_prose(self, cfg, locale) -> None:
        # The finding this fixes: localization tracked the GATE layer while the
        # CLASSIFIER layer stayed English, so a German user hit English prose
        # in the {detail} slot of a translated shell - exactly when it helps
        # least.
        cfg.locale = locale
        english = i18n.STRINGS["en"]["error_docker_permission_denied"]
        detail = error_classes.classify(PermissionError(13, "denied"), cfg).detail
        assert detail != english, f"{locale} still shows the English text"
        assert detail == i18n.STRINGS[locale]["error_docker_permission_denied"]

    def test_the_reference_and_the_raw_line_survive_translation(self, cfg) -> None:
        cfg.locale = "de"
        detail = error_classes.classify("pull access denied for ghcr.io/owner/app", cfg).detail
        assert "ghcr.io/owner/app:1.0.0" in detail, "the reference must stay in the message"
        assert "pull access denied" in detail, "the raw engine line must stay quotable for a bug report"


class TestUnclassifiedStaysVisible:
    def test_an_unmodelled_cause_is_not_dressed_up(self, cfg) -> None:
        # An unnamed cause must LOOK unnamed, so it reads as a gap to close
        # rather than as a class that happens to be terse.
        result = error_classes.classify("some entirely unmodelled engine burp", cfg)
        assert result.id == check_ids.UNCLASSIFIED
        assert not result.is_classified
        assert result.detail == "some entirely unmodelled engine burp"

    def test_the_class_is_logged_even_where_the_return_type_cannot_carry_it(self, cfg, caplog) -> None:
        # (ok, message) call sites cannot express an id yet, so the class goes
        # into the log - visible in a bug report, which is where it is needed.
        with caplog.at_level("INFO"):
            error_classes.detail_of(PermissionError(13, "denied"), cfg)
        assert any("docker_permission_denied" in r.getMessage() for r in caplog.records)

    def test_an_unclassified_cause_says_so_in_the_log(self, cfg, caplog) -> None:
        with caplog.at_level("INFO"):
            error_classes.detail_of("unmodelled", cfg)
        assert any("unclassified" in r.getMessage() for r in caplog.records)


class TestOneEntryPoint:
    def test_an_exception_is_tried_as_an_exception_before_its_text(self, cfg) -> None:
        # A StoreError whose text happens to contain a refusal marker must
        # still be the credential class: the ordering is what makes the answer
        # independent of which layer caught it.
        assert error_classes.classify(StoreError("access denied by helper"), cfg).id == "credential_helper_broken"

    def test_both_legacy_helpers_now_delegate(self) -> None:
        import inspect

        for fn in (dockerfile_backend._classified_detail, image_backend._classify_pull_error):
            assert "error_classes" in inspect.getsource(fn), f"{fn.__name__} classifies on its own again"
