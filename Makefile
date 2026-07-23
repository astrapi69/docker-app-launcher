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

.PHONY: test-gui
test-gui: ## Run the real-window GUI tests (needs a display or xvfb-run)
	poetry run pytest tests/test_gui_window.py -v --no-cov

.PHONY: screenshots
screenshots: ## GUI screenshots (all three frontends, dark) into test-screenshots/
	rm -rf test-screenshots/
	DAL_SCREENSHOTS=1 poetry run pytest tests/test_gui_window.py tests/test_gui_ctk.py tests/test_gui_qt.py -q --no-cov
	@ls test-screenshots/ 2>/dev/null && echo "-> test-screenshots/" || echo "no screenshots produced (no capture backend available)"

# ---------------------------------------------------------------------------
# Manual Launcher Testing
# ---------------------------------------------------------------------------

# Which config the launcher-* targets drive. Override per invocation, e.g.
#   make launcher-test TEST_CONFIG=test-configs/bibliogon.json
TEST_CONFIG ?= test-configs/adaptive-learner.json

.PHONY: launcher-test
launcher-test: ## Start launcher GUI with $(TEST_CONFIG) (debug)
	poetry run docker-app-launcher --config $(TEST_CONFIG) --debug

.PHONY: launcher-status
launcher-status: ## Print app state with $(TEST_CONFIG) and exit
	poetry run docker-app-launcher --config $(TEST_CONFIG) --status

.PHONY: launcher-check
launcher-check: ## Check Docker availability with $(TEST_CONFIG) and exit
	poetry run docker-app-launcher --config $(TEST_CONFIG) --check

.PHONY: launcher-stop
launcher-stop: ## Stop the app defined by $(TEST_CONFIG)
	poetry run docker-app-launcher --config $(TEST_CONFIG) --stop

.PHONY: launcher-cleanup
launcher-cleanup: ## Remove stale leftovers for $(TEST_CONFIG)
	poetry run docker-app-launcher --config $(TEST_CONFIG) --cleanup

.PHONY: launcher-version
launcher-version: ## Print the launcher version
	poetry run docker-app-launcher --version

.PHONY: launcher-test-al
launcher-test-al: ## Manual GUI test with the Adaptive Learner config
	$(MAKE) launcher-test TEST_CONFIG=test-configs/adaptive-learner.json

.PHONY: launcher-test-bibliogon
launcher-test-bibliogon: ## Manual GUI test with the Bibliogon config
	$(MAKE) launcher-test TEST_CONFIG=test-configs/bibliogon.json

.PHONY: launcher-test-minimal
launcher-test-minimal: ## Manual GUI test with the minimal (defaults-only) config
	$(MAKE) launcher-test TEST_CONFIG=test-configs/minimal.json

.PHONY: smoke
smoke: ## Smoke test: version + each test-config parses and --check runs
	@echo "=== Smoke Test ==="
	poetry run docker-app-launcher --version
	poetry run docker-app-launcher --config test-configs/minimal.json --check || true
	poetry run docker-app-launcher --config test-configs/adaptive-learner.json --check || true
	poetry run docker-app-launcher --config test-configs/bibliogon.json --check || true
	@echo "=== Smoke OK ==="

# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------

.PHONY: ci
ci: lint format-check typecheck test ## Full CI pipeline (lint + format + types + test)

# ---------------------------------------------------------------------------
# Version Management
# ---------------------------------------------------------------------------

# After a bump, reinstall so the venv's package metadata matches: __version__
# is read from the installed dist-info at runtime, not from source.
.PHONY: bump-patch
bump-patch: ## Bump patch version (0.1.0 -> 0.1.1)
	poetry version patch
	poetry install --only-root

.PHONY: bump-minor
bump-minor: ## Bump minor version (0.1.0 -> 0.2.0)
	poetry version minor
	poetry install --only-root

.PHONY: bump-major
bump-major: ## Bump major version (0.1.0 -> 1.0.0)
	poetry version major
	poetry install --only-root

# ---------------------------------------------------------------------------
# Build & Publish
# ---------------------------------------------------------------------------

.PHONY: build
build: ## Build distribution package (sdist + wheel); cleans stale artifacts first
	rm -rf dist/
	poetry build

.PHONY: build-check
build-check: build ## Build, then validate artifacts with twine + inspect wheel
	poetry run twine check dist/*
	for whl in dist/*.whl; do poetry run python -m zipfile -l "$$whl"; done

.PHONY: release-check
release-check: ci codespell build-check ## Full pre-release gate (CI + spell + build + twine)
	@echo "Release gate passed. See .claude/rules/release-workflow.md for the full checklist."

.PHONY: publish-test
publish-test: release-check ## Publish to TestPyPI (asks for confirmation first)
	@V=$$(poetry version -s); \
	if curl -sfo /dev/null "https://test.pypi.org/pypi/docker-app-launcher/$$V/json"; then \
		echo "ERROR: version $$V already exists on TestPyPI. Bump the version first (poetry version patch)."; \
		exit 1; \
	fi; \
	printf "Upload docker-app-launcher %s to TestPyPI? [y/N] " "$$V"; \
	read answer; case "$$answer" in y|Y) ;; *) echo "Aborted."; exit 1;; esac; \
	poetry publish -r testpypi

.PHONY: publish
publish: release-check ## Run full gate, confirm, then publish to PyPI (irreversible)
	@V=$$(poetry version -s); \
	if curl -sfo /dev/null "https://pypi.org/pypi/docker-app-launcher/$$V/json"; then \
		echo "ERROR: version $$V already exists on PyPI. Bump the version first (poetry version patch)."; \
		exit 1; \
	fi; \
	printf "Upload docker-app-launcher %s to PyPI? IRREVERSIBLE. [y/N] " "$$V"; \
	read answer; case "$$answer" in y|Y) ;; *) echo "Aborted."; exit 1;; esac; \
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
