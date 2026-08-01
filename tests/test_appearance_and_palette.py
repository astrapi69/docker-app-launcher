"""#118: the appearance detection and the palette it feeds.

The defect being replaced: ``darkdetect`` reports Light on a KDE desktop
where the XDG portal and Qt both report dark, because it really answers "does
the GTK theme name contain -dark". The launcher inherited that answer through
``ctk.set_appearance_mode("system")`` - the one line in the whole project that
claimed to follow the system, on a machine where it did not.

So the tests here are about the two properties that would let the same thing
happen again: the unknown case must stay distinguishable from light, and the
merge down to two values must happen in exactly one readable place.
"""

from __future__ import annotations

import pytest

from docker_app_launcher import appearance, palette


@pytest.fixture(autouse=True)
def _fresh_cache():
    appearance.reset_cache()
    yield
    appearance.reset_cache()


class TestThreeValues:
    def test_no_preference_is_its_own_answer(self) -> None:
        # Two values would force the unknown case onto one of the others -
        # which is the failure being replaced, not a style preference.
        assert appearance.NO_PREFERENCE not in (appearance.LIGHT, appearance.DARK)
        assert set(appearance.APPEARANCES) == {appearance.LIGHT, appearance.DARK, appearance.NO_PREFERENCE}

    def test_a_missing_tool_is_no_preference_never_light(self, monkeypatch) -> None:
        # The measured requirement: where nothing can be asked, the answer is
        # "no preference". A wrong "light" here IS the bug.
        monkeypatch.setattr("docker_app_launcher.appearance.shutil.which", lambda _name: None)
        for probe in (appearance._linux, appearance._macos, appearance._windows):
            verdict, why = probe()
            assert verdict == appearance.NO_PREFERENCE, f"{probe.__name__} answered {verdict} with no tool present"
            assert why, f"{probe.__name__} gave no reason - a verdict with no trace is what hid the defect"

    def test_an_unreachable_portal_is_no_preference(self, monkeypatch) -> None:
        monkeypatch.setattr(appearance, "_run", lambda argv: (True, 1, "Error connecting: no session bus"))
        verdict, why = appearance._linux()
        assert verdict == appearance.NO_PREFERENCE
        assert "unreachable" in why

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("(<<uint32 1>>,)", appearance.DARK),
            ("(<<uint32 2>>,)", appearance.LIGHT),
            ("(<<uint32 0>>,)", appearance.NO_PREFERENCE),
        ],
    )
    def test_portal_values_follow_the_freedesktop_meaning(self, monkeypatch, output, expected) -> None:
        monkeypatch.setattr(appearance, "_run", lambda argv: (True, 0, output))
        assert appearance._linux()[0] == expected


class TestPlatformFacts:
    """Two things the platforms decide, not us - measured on real runners."""

    def test_macos_absent_key_is_light_not_unknown(self, monkeypatch) -> None:
        # AppleInterfaceStyle exists ONLY in dark mode; its absence is the
        # documented representation of light. Measured on macos-latest:
        # rc=1, "The domain/default pair ... does not exist", machine light.
        monkeypatch.setattr(appearance, "_run", lambda argv: (True, 1, "does not exist"))
        verdict, why = appearance._macos()
        assert verdict == appearance.LIGHT
        assert "absent" in why

    def test_macos_missing_tool_is_still_no_preference(self, monkeypatch) -> None:
        # The distinction the measurement script got WRONG on its first run:
        # a missing tool is not a missing key. It reported macOS as light
        # while running on Linux, where 'defaults' simply does not exist.
        monkeypatch.setattr(appearance, "_run", lambda argv: (False, -1, "defaults not present"))
        assert appearance._macos()[0] == appearance.NO_PREFERENCE

    def test_windows_unset_value_is_no_preference_not_light(self, monkeypatch) -> None:
        # Windows writes these only once the user has touched the setting.
        monkeypatch.setattr(appearance, "_run", lambda argv: (True, 1, "ERROR: The system was unable to find"))
        verdict, why = appearance._windows()
        assert verdict == appearance.NO_PREFERENCE
        assert "never chosen" in why

    def test_windows_reads_the_APPLICATION_value(self) -> None:
        # AppsUseLightTheme (apps) and SystemUsesLightTheme (shell) are
        # separate and can differ; a window follows the first. Reading the
        # other would be a confident answer to a question nobody asked.
        import inspect

        source = inspect.getsource(appearance._windows)
        assert "AppsUseLightTheme" in source
        assert "SystemUsesLightTheme" not in source

    @pytest.mark.parametrize(("value", "expected"), [("0x0", appearance.DARK), ("0x1", appearance.LIGHT)])
    def test_windows_values(self, monkeypatch, value, expected) -> None:
        monkeypatch.setattr(
            appearance, "_run", lambda argv: (True, 0, f"    AppsUseLightTheme    REG_DWORD    {value}")
        )
        assert appearance._windows()[0] == expected


class TestTheMergeHappensOnce:
    def test_config_always_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(appearance, "detect_system_appearance", lambda: (appearance.DARK, "detected dark"))
        assert appearance.effective_appearance(appearance.LIGHT)[0] == appearance.LIGHT
        assert appearance.effective_appearance(appearance.DARK)[0] == appearance.DARK

    def test_no_preference_resolves_to_light_and_says_so(self, monkeypatch) -> None:
        monkeypatch.setattr(appearance, "detect_system_appearance", lambda: (appearance.NO_PREFERENCE, "portal said 0"))
        result, why = appearance.effective_appearance("system")
        assert result == appearance.LIGHT
        # A named decision, not a silent fallback: the trace is what would have
        # exposed the defect this replaces, where a wrong answer with no log
        # looked exactly like a design choice.
        assert "falling back to light" in why
        assert "portal said 0" in why

    def test_detection_is_logged(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(appearance, "_run", lambda argv: (True, 0, "(<<uint32 1>>,)"))
        monkeypatch.setattr("docker_app_launcher.appearance.sys.platform", "linux")
        with caplog.at_level("INFO"):
            appearance.detect_system_appearance()
        assert any("system appearance" in r.getMessage() for r in caplog.records), (
            "the detection must leave a trace: a wrong answer with no log looks like a design choice"
        )

    def test_palette_refuses_the_unresolved_value(self) -> None:
        # A second place that quietly maps no_preference would be the drift
        # this whole issue is about.
        with pytest.raises(ValueError, match="effective_appearance"):
            palette.palette_for(appearance.NO_PREFERENCE)


def _relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of an #rrggbb value."""
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    adjusted = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]


def _contrast(fg: str, bg: str) -> float:
    light, dark = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class TestPaletteContrast:
    """The accessibility promise must survive the dark theme.

    Status is already never signalled by colour alone (there is always a
    ✓/✗/· symbol). That promise is undercut, not kept, if the dark palette's
    red is unreadable on the dark background.
    """

    @pytest.mark.parametrize("meaning", palette.TEXT_MEANINGS)
    def test_the_new_dark_palette_clears_wcag_aa(self, meaning) -> None:
        # New work meets the bar. The dark values are chosen here, so there is
        # nothing to inherit and no excuse.
        pal = palette.DARK_PALETTE
        ratio = _contrast(getattr(pal, meaning), pal.background)
        assert ratio >= 4.5, f"dark/{meaning} contrast {ratio:.2f}:1 is below the 4.5:1 AA threshold"

    @pytest.mark.parametrize("meaning", palette.TEXT_MEANINGS)
    def test_the_inherited_light_palette_does_not_get_worse(self, meaning) -> None:
        """The light palette reproduces colours that were NEVER contrast-checked.

        MEASURED when this module landed, against the window background
        #d9d9d9:

            foreground 11.41  muted 8.95  link 4.52   -> AA ok
            success     3.56  error 4.11  warning 3.29 -> BELOW the 4.5:1
                                                          AA threshold

        Three of the six status colours the launcher has shipped for its whole
        life do not clear AA for body text. Found by this test on its first
        run, not by anyone looking.

        The threshold is NOT loosened to make this green - that would be
        teaching the checker to ignore the hit. The existing numbers are
        pinned as a FLOOR so they cannot quietly get worse, and raising them
        changes today's appearance, which is a decision for its own change.
        """
        # The MEASURED values, to two decimals, minus nothing. Pinned as a
        # floor: they may improve, never decay.
        floors = {
            "foreground": 11.41,
            "muted": 8.95,
            "success": 3.56,
            "error": 4.11,
            "warning": 3.29,
            "link": 4.52,
        }
        pal = palette.LIGHT_PALETTE
        # Rounded to the two decimals the floors are written in: comparing raw
        # floats against a printed value fails on the printing, not on a
        # regression - a gate that goes red for the wrong reason.
        ratio = round(_contrast(getattr(pal, meaning), pal.background), 2)
        assert ratio >= floors[meaning], f"light/{meaning} dropped to {ratio}:1, below the pinned {floors[meaning]}"

    def test_meanings_pin_matches_the_dataclass(self) -> None:
        from dataclasses import fields

        assert tuple(f.name for f in fields(palette.Palette)) == palette.MEANINGS, (
            "a new palette field must be registered in MEANINGS, or it ships unmeasured for contrast"
        )

    def test_no_two_meanings_share_a_value(self) -> None:
        # The finding that motivated the module: #555 and #333333 were two
        # values for one meaning. With a palette that cannot recur.
        for name in (appearance.LIGHT, appearance.DARK):
            pal = palette.palette_for(name)
            values = [getattr(pal, m) for m in palette.MEANINGS]
            assert len(values) == len(set(values)), f"{name} palette has duplicate values: {values}"

    def test_light_palette_reproduces_todays_colours(self) -> None:
        # Landing the palette must change nothing visually; the dark variant
        # is the new thing, the light one is a refactor.
        pal = palette.LIGHT_PALETTE
        assert (pal.success, pal.error, pal.warning, pal.link) == ("#188038", "#c5221f", "#b06000", "#2a5db0")


class TestNoColourLiteralsRemain:
    """The palette only helps if nobody bypasses it.

    Before this change the frontends carried 20 hardcoded values across four
    files - including two different values for one meaning (#555 and #333333
    for muted text), which is precisely the kind of thing nobody notices
    without a palette and which cannot arise with one.
    """

    def test_frontends_carry_no_hex_colours(self) -> None:
        import re
        from pathlib import Path

        frontends = Path(__file__).resolve().parents[1] / "src" / "docker_app_launcher" / "frontends"
        offenders: list[str] = []
        for path in sorted(frontends.glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if re.search(r'"#[0-9a-fA-F]{3,6}"', line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert not offenders, "colour literals bypass the palette:\n" + "\n".join(offenders)

    def test_the_light_palette_is_what_they_now_use(self) -> None:
        # The replacement must be a refactor, not a redesign: the values the
        # frontends resolve to are exactly the ones they had.
        from docker_app_launcher.frontends import ctk_window

        assert ctk_window._OK_COLOR == "#188038"
        assert ctk_window._ERR_COLOR == "#c5221f"
