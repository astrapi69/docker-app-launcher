"""python-poetry-template — a modern Python project starter."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("python-poetry-template")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"
