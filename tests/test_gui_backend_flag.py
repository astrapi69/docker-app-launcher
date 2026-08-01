"""#119: choosing the window toolkit at start, and refusing a dead end.

Three frontends are built, parity-checked and shipped - but the choice was a
config field only, so seeing another one meant editing a JSON file. This is
the small half of #119: the flag. The visible switch inside the window has
open questions (a toolkit cannot be swapped in a running process) and is not
part of it.

The refusals are the substance here, not the override: a selection that
leads nowhere is worse than no selection, so an unknown name and a missing
extra must both end in a MESSAGE with the way out, never in a traceback and
never in a silently ignored flag.
"""

from __future__ import annotations

import pytest

from docker_app_launcher import __main__ as cli
from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.frontends import available_frontends


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "launcher.json"
    path.write_text('{"app_name": "Flag App", "gui_backend": "tk", "single_instance": false}\n', encoding="utf-8")
    return path


class TestOverride:
    def test_the_flag_wins_over_the_config_field(self, config_file, monkeypatch) -> None:
        seen: dict[str, str] = {}

        def fake_open(config: LauncherConfig, *, debug: bool, preview_state: str | None = None) -> int:
            seen["backend"] = config.gui_backend
            return 0

        monkeypatch.setattr(cli, "_open_frontend", fake_open)
        assert cli.main(["--config", str(config_file), "--gui-backend", "qt"]) == 0
        assert seen["backend"] == "qt", "the flag did not override the config field"

    def test_without_the_flag_the_config_field_stands(self, config_file, monkeypatch) -> None:
        seen: dict[str, str] = {}

        def fake_open(config: LauncherConfig, *, debug: bool, preview_state: str | None = None) -> int:
            seen["backend"] = config.gui_backend
            return 0

        monkeypatch.setattr(cli, "_open_frontend", fake_open)
        assert cli.main(["--config", str(config_file)]) == 0
        assert seen["backend"] == "tk"

    def test_every_shipped_frontend_is_accepted(self) -> None:
        # Not a copy of the list: whatever the registry knows must be usable.
        for name in available_frontends():
            assert cli._frontend_refusal(name) is None, f"{name} is registered but refused"


class TestRefusals:
    def test_unknown_name_exits_two_and_names_the_known_ones(self, config_file, capsys) -> None:
        rc = cli.main(["--config", str(config_file), "--gui-backend", "swing"])
        assert rc == 2, "a usage error must exit 2 per the documented contract"
        message = capsys.readouterr().err
        assert "swing" in message
        for name in available_frontends():
            assert name in message, f"the refusal does not name {name} as an option"

    def test_a_typo_is_refused_even_on_a_cli_only_run(self, config_file, capsys, monkeypatch) -> None:
        # The window is never opened here, so a lazily-validated flag would be
        # silently ignored and only surface at some later, unrelated start.
        monkeypatch.setattr(cli, "run_cli_action", lambda *a, **k: 0)
        rc = cli.main(["--config", str(config_file), "--gui-backend", "swing", "--status"])
        assert rc == 2
        assert "swing" in capsys.readouterr().err

    def test_a_missing_extra_becomes_a_message_not_a_traceback(self, config_file, capsys, monkeypatch) -> None:
        # ctk/qt import fine WITHOUT their extra and refuse in run() - that
        # RuntimeError already carries the pip hint, and it must reach the user
        # as an instruction rather than as a crash.
        from docker_app_launcher import frontends

        class _Refusing:
            @staticmethod
            def run(config, *, debug=False, preview_state=None):
                raise RuntimeError("the Qt frontend requires the 'qt' extra: pip install docker-app-launcher[qt]")

        monkeypatch.setattr(frontends, "get_frontend", lambda name: _Refusing)
        monkeypatch.setattr(cli, "run_cli_action", lambda *a, **k: None)
        rc = cli.main(["--config", str(config_file)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "pip install docker-app-launcher[qt]" in err, "the way out must be in the message"
        assert "Traceback" not in err


class TestOneDoorForEveryWindow:
    def test_preview_and_normal_launch_share_the_open_path(self, config_file, monkeypatch) -> None:
        # One helper translates a missing extra; a second window-opening call
        # site that bypassed it would reintroduce the traceback for exactly one
        # entry point - the drift class the parity suites exist for.
        calls: list[str | None] = []

        def fake_open(config: LauncherConfig, *, debug: bool, preview_state: str | None = None) -> int:
            calls.append(preview_state)
            return 0

        monkeypatch.setattr(cli, "_open_frontend", fake_open)
        assert cli.main(["--config", str(config_file)]) == 0
        assert cli.main(["--config", str(config_file), "--preview", "fresh"]) == 0
        assert calls == [None, "fresh"]
