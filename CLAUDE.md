# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## What this is

A Poetry-based Python project template (PEP 621, `src/` layout). When used as a
starter, the package `python_poetry_template` is renamed to the new project's name.

## Layout

- `src/python_poetry_template/` — package code (`cli.py` is the example entry point)
- `tests/` — pytest suite
- `pyproject.toml` — single source of truth for metadata and tool config

## Commands

- Install: `poetry install --with dev`
- Run everything CI runs: `make ci`
- Tests: `make test` (with coverage) or `make test-fast`
- Lint / format / types: `make lint`, `make format`, `make typecheck`
- Auto-fix: `make fix`

## Conventions

- **Formatting & linting:** Ruff only (no Black). Run `make fix` before committing.
- **Typing:** mypy runs in `strict` mode. Add type hints to all new code.
- **Line length:** 100.
- **Imports:** sorted by Ruff's isort; first-party = the package name.
- **Tests:** every new public function should have a test; keep coverage meaningful.
- **Python:** target 3.10+; CI verifies 3.10 – 3.14.

## When renaming the package

Update the package directory, then `name` / `[project.scripts]` /
`[tool.poetry] packages` / `known-first-party` / `--cov` targets in `pyproject.toml`,
and the imports in `tests/`.
