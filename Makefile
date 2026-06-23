.DEFAULT_GOAL := help
SHELL := /bin/bash

SRC_DIRS := src/ tests/
PKG := docker_app_launcher

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: lock-install
lock-install: ## Lock and install project dependencies
	poetry lock
	poetry install --with dev

.PHONY: install
install: ## Install project with dev dependencies
	poetry install --with dev

.PHONY: update
update: ## Update dependencies to latest allowed versions
	poetry update

.PHONY: hooks
hooks: ## Install pre-commit + commit-msg hooks
	poetry run pre-commit install --install-hooks

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

.PHONY: lint
lint: ## Run ruff linter
	poetry run ruff check $(SRC_DIRS)

.PHONY: lint-fix
lint-fix: ## Run ruff linter with auto-fix
	poetry run ruff check $(SRC_DIRS) --fix

.PHONY: format
format: ## Format code with ruff
	poetry run ruff format $(SRC_DIRS)

.PHONY: format-check
format-check: ## Check formatting without changes
	poetry run ruff format --check $(SRC_DIRS)

.PHONY: typecheck
typecheck: ## Run mypy type checks
	poetry run mypy $(SRC_DIRS)

.PHONY: codespell
codespell: ## Run codespell
	poetry run codespell $(SRC_DIRS)

.PHONY: codespell-fix
codespell-fix: ## Run codespell with auto-fix
	poetry run codespell $(SRC_DIRS) --write-changes

.PHONY: precommit
precommit: ## Run all pre-commit hooks on all files
	poetry run pre-commit run -a

.PHONY: fix
fix: ## Run all auto-fixes (ruff lint + format)
	poetry run ruff check $(SRC_DIRS) --fix
	poetry run ruff format $(SRC_DIRS)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test
test: ## Run all tests
	poetry run pytest

.PHONY: test-v
test-v: ## Run all tests (verbose)
	poetry run pytest -v

.PHONY: test-fast
test-fast: ## Run tests without coverage (faster)
	poetry run pytest -q --maxfail=1 --disable-warnings --no-cov

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	poetry run pytest --cov=$(PKG) --cov-report=term-missing

.PHONY: test-xml
test-xml: ## Run tests with XML coverage (for CI)
	poetry run pytest -q --maxfail=1 --disable-warnings --cov=$(PKG) --cov-report=xml

# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------

.PHONY: ci
ci: lint format-check typecheck test ## Full CI pipeline (lint + format + types + test)

# ---------------------------------------------------------------------------
# Version Management
# ---------------------------------------------------------------------------

.PHONY: bump-patch
bump-patch: ## Bump patch version (0.1.0 -> 0.1.1)
	poetry version patch

.PHONY: bump-minor
bump-minor: ## Bump minor version (0.1.0 -> 0.2.0)
	poetry version minor

.PHONY: bump-major
bump-major: ## Bump major version (0.1.0 -> 1.0.0)
	poetry version major

# ---------------------------------------------------------------------------
# Build & Publish
# ---------------------------------------------------------------------------

.PHONY: build
build: ## Build distribution package (sdist + wheel)
	poetry build

.PHONY: build-check
build-check: build ## Build, then validate artifacts with twine + inspect wheel
	poetry run twine check dist/*
	poetry run python -m zipfile -l dist/*.whl

.PHONY: release-check
release-check: ci codespell build-check ## Full pre-release gate (CI + spell + build + twine)
	@echo "Release gate passed. See .claude/rules/release-workflow.md for the full checklist."

.PHONY: publish-test
publish-test: release-check ## Publish to TestPyPI (smoke-test before the real thing)
	poetry publish -r testpypi

.PHONY: publish
publish: release-check ## Run full gate, then publish to PyPI (irreversible)
	poetry publish

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build artifacts and caches
	rm -rf dist/ build/ .pytest_cache/ .ruff_cache/ .mypy_cache/ .coverage coverage.xml htmlcov/
	find src/ tests/ -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete

.PHONY: clean-venv
clean-venv: ## Remove Poetry virtualenv
	poetry env remove --all || true

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
