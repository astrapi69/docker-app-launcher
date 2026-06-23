"""Tests for ``subprocess_utils`` and a lint-like guard ensuring no subprocess
call in the package forgets the window-suppressing kwargs.

On Windows every uncovered ``subprocess.run``/``Popen`` flashes a CMD window;
during an install that means dozens of windows popping up - the regression we
are guarding against.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from docker_app_launcher import subprocess_utils

SRC = Path(subprocess_utils.__file__).parent

# ``CREATE_NO_WINDOW`` only exists on Windows; the value is a stable Win32
# constant, so we can assert against it directly without importing it.
CREATE_NO_WINDOW = 0x08000000


class TestSubprocessKwargs:
    def test_windows_sets_create_no_window(self, monkeypatch) -> None:
        # Provide the flag so the win32 branch runs on any platform.
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        assert subprocess_utils.subprocess_kwargs() == {"creationflags": CREATE_NO_WINDOW}

    @pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd"])
    def test_non_windows_returns_empty(self, monkeypatch, platform: str) -> None:
        monkeypatch.setattr(sys, "platform", platform)
        assert subprocess_utils.subprocess_kwargs() == {}

    def test_result_is_a_fresh_dict(self, monkeypatch) -> None:
        # Callers splat the result; it must never be a shared mutable singleton.
        monkeypatch.setattr(sys, "platform", "linux")
        first = subprocess_utils.subprocess_kwargs()
        first["creationflags"] = 1
        assert subprocess_utils.subprocess_kwargs() == {}


def _subprocess_calls_missing_kwargs(tree: ast.AST) -> list[int]:
    """Return line numbers of ``subprocess.run``/``Popen`` calls that do not
    splat ``**subprocess_kwargs()`` into their arguments."""
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in {"run", "Popen"}
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        has_kwargs = any(
            kw.arg is None
            and isinstance(kw.value, ast.Call)
            and isinstance(kw.value.func, ast.Name)
            and kw.value.func.id == "subprocess_kwargs"
            for kw in node.keywords
        )
        if not has_kwargs:
            offenders.append(node.lineno)
    return offenders


class TestNoUnguardedSubprocessCall:
    def test_every_subprocess_call_suppresses_windows(self) -> None:
        problems: dict[str, list[int]] = {}
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            missing = _subprocess_calls_missing_kwargs(tree)
            if missing:
                problems[str(path.relative_to(SRC))] = missing
        assert not problems, (
            "subprocess.run/Popen calls missing **subprocess_kwargs() "
            f"(would flash a console window on Windows): {problems}"
        )
