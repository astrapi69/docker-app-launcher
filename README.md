# python-poetry-template

A lightweight, ready-to-use template for modern Python projects using [Poetry](https://python-poetry.org/) (2.x / PEP 621).
Ideal for quick prototyping, scripting projects, or laying the foundation for scalable applications.

---

## Features

- **PEP 621 `pyproject.toml`** with native Poetry 2.x metadata
- **`src/` layout** — import-safe, packaging-friendly project structure
- **Pytest** test suite with branch coverage via `pytest-cov`
- **Ruff** for linting **and** formatting (one fast tool, no Black needed)
- **mypy** in `strict` mode for type checking, with `py.typed` marker
- **codespell** for catching typos
- **Pre-commit hooks** (ruff, mypy, codespell, hygiene checks)
- **GitHub Actions CI** matrix across Python 3.10 – 3.14
- **Makefile** for common development tasks
- Typed example CLI with `argparse` and `--version`

---

## Getting Started

### 1. Create your project from this template

Click **Use this template** → **Create a new repository**, then clone it:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_PROJECT_NAME.git
cd YOUR_PROJECT_NAME
```

### 2. Rename the package

Replace the placeholder name in these spots:

- `src/python_poetry_template/` → `src/your_package_name/`
- `pyproject.toml` → `name`, `[project.scripts]`, `[tool.poetry] packages`,
  `[tool.ruff.lint.isort] known-first-party`, `--cov` targets
- imports in `tests/`

### 3. Install dependencies

Make sure [Poetry ≥ 2.0](https://python-poetry.org/docs/#installation) is installed, then:

```bash
make install
# or
poetry install --with dev
```

### 4. Install pre-commit hooks

```bash
make hooks
# or
poetry run pre-commit install --install-hooks
```

### 5. Run the CLI

```bash
poetry run template-script --name Asterios
poetry run template-script --version
```

### 6. Run the checks

```bash
make ci      # lint + format-check + typecheck + test
make test    # tests only
```

---

## Makefile Commands

Run `make help` to see all available targets:

| Command            | Description                                       |
|--------------------|---------------------------------------------------|
| `make install`     | Install project with dev dependencies             |
| `make update`      | Update dependencies                               |
| `make hooks`       | Install pre-commit + commit-msg hooks             |
| `make lint`        | Run ruff linter                                   |
| `make lint-fix`    | Run ruff linter with auto-fix                     |
| `make format`      | Format code with ruff                             |
| `make format-check`| Check formatting without changes                  |
| `make typecheck`   | Run mypy type checks                              |
| `make codespell`   | Run codespell spell checker                       |
| `make fix`         | Run all auto-fixes (ruff lint + format)           |
| `make precommit`   | Run all pre-commit hooks                          |
| `make test`        | Run all tests                                     |
| `make test-cov`    | Run tests with coverage report                    |
| `make ci`          | Full CI pipeline (lint + format + types + test)   |
| `make build`       | Build distribution package                        |
| `make publish`     | Publish to PyPI                                   |
| `make bump-patch`  | Bump patch version                                |
| `make clean`       | Remove build artifacts and caches                 |

---

## Project Structure

```text
python-poetry-template/
├── .github/workflows/ci.yml   # GitHub Actions CI (Python 3.10–3.14)
├── .pre-commit-config.yaml    # Pre-commit hook configuration
├── .env.example               # Environment variable template
├── LICENSE                    # MIT license
├── Makefile                   # Development task automation
├── pyproject.toml             # PEP 621 metadata + tool config
├── poetry.lock                # Exact version locks for reproducibility
├── README.md                  # You're reading it
├── src/
│   └── python_poetry_template/
│       ├── __init__.py        # Package version
│       ├── cli.py             # Example typed CLI entry point
│       └── py.typed           # PEP 561 typing marker
└── tests/
    ├── __init__.py
    └── test_example.py
```

---

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

> Created with love by Asterios Raptis
