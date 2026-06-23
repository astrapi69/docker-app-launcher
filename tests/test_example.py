"""Example tests for the template package."""

from __future__ import annotations

import pytest

from python_poetry_template.cli import build_parser, greet, main


def test_greet_default() -> None:
    assert greet() == "Hello, world!"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Asterios", "Hello, Asterios!"),
        ("", "Hello, !"),
    ],
)
def test_greet_with_name(name: str, expected: str) -> None:
    assert greet(name) == expected


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.name == "world"


def test_main_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--name", "Poetry"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "Hello, Poetry!"
