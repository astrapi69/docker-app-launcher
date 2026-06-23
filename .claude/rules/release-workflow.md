# Release workflow

Permanent workflow for releasing a new version to PyPI. AI assistants
read this file when a release is due.

Prompt triggers: "release new version", "new release", "publish to
PyPI", "cut release".

In this project the import package is `docker_app_launcher` and the
PyPI distribution name is `docker-app-launcher`.

---

## Ground rules

- Do not skip manual steps. The checklist at the end is mandatory.
- Every release is a logical boundary: do not release mid-feature.
- Tests must be green. Red tests block the release, no exceptions.
- The CHANGELOG is for humans: do not paste raw commit messages,
  summarize meaningfully.
- Version bump follows SemVer strictly, even in the 0.x phase.

---

## Step 1: Capture the current state

```bash
git tag --sort=-creatordate | head -5            # latest tags
LAST_TAG=$(git describe --tags --abbrev=0)
git log ${LAST_TAG}..HEAD --oneline --no-merges  # commits since last tag
git diff ${LAST_TAG}..HEAD --stat | tail -1      # statistics
grep -H "^version" pyproject.toml                # current version
```

Show the user the summary and wait for confirmation before continuing.

---

## Step 2: Version bump per SemVer

The public API contract is the module-level exports of
`docker_app_launcher` (`LauncherConfig`, `launch`) plus the documented
`actions` functions, the `LauncherConfig` field set, and the CLI flags.

| Change | Bump |
|--------|------|
| Remove/rename a public function/class/field/CLI flag, change a signature incompatibly, change a return type | **Major** |
| Add a public function/class/field, add a keyword-only param with default, add an optional dependency/extra | **Minor** |
| Bug fix / perf / internal refactor with no public-signature change | **Patch** |

`feat:` → minor, `fix:`/`perf:`/`refactor:` → patch, `BREAKING CHANGE`
or `!` → major. Adding a `DeprecationWarning` is minor; removing the
deprecated thing is major. In 0.x a major bump is rare — breaking
changes usually become a minor bump with a prominent CHANGELOG section.

Propose the new version with rationale. Wait for user OK.

---

## Step 3: Generate the CHANGELOG entry

Move items out of `[Unreleased]` into a new `## [X.Y.Z] - YYYY-MM-DD`
section. Group, in this order: **Breaking Changes**, **Added**,
**Changed**, **Deprecated**, **Removed**, **Fixed**, **Security**.
Summarize meaningfully; collapse multiple commits per feature. Update
the compare/tag links at the bottom of `CHANGELOG.md`.

Commit: `docs: changelog for vX.Y.Z`

---

## Step 4: Bump version

```bash
poetry version <patch|minor|major>   # or an explicit: poetry version 0.2.0
```

`docker_app_launcher/__init__.py` derives `__version__` from
`importlib.metadata.version("docker-app-launcher")`, so it tracks
`pyproject.toml` automatically — nothing to edit there. Verify:

```bash
grep -H "^version" pyproject.toml
poetry run python -c "import docker_app_launcher; print(docker_app_launcher.__version__)"
```

Commit: `chore(release): bump version to vX.Y.Z`

---

## Step 5: Run the mandatory check chain

```bash
make release-check    # = ci (lint+format+types+test) + codespell + build + twine check
poetry run pre-commit run --all-files
```

ALL must be green. On a red check: abort the release, fix the problem
in its own commit, restart from Step 1.

---

## Step 6: Build & inspect

```bash
make build-check      # poetry build + twine check + wheel listing
```

Inspect the wheel listing — make sure nothing unintended shipped and
that `py.typed` is present. On a build error: stop, report, fix,
restart.

---

## Step 7: Publish to TestPyPI first

Publishing to PyPI is irreversible. Always smoke-test via TestPyPI for
any non-trivial release.

```bash
# One-time configuration
poetry config repositories.testpypi https://test.pypi.org/legacy/
poetry config pypi-token.testpypi <token>

make publish-test     # gated build + upload to TestPyPI
```

Smoke-install from TestPyPI in a clean venv:

```bash
# IMPORTANT: cd OUT of the repo first. A venv/pip-install inside the
# repo tree leaks the local working copy onto the import path, so
# `import docker_app_launcher` may import in-tree source instead of the
# installed wheel — silently masking a broken sdist/wheel build.
cd ..
python -m venv /tmp/testinstall
source /tmp/testinstall/bin/activate
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            docker-app-launcher==<new-version>
python -c "import docker_app_launcher; print(docker_app_launcher.__version__)"
docker-app-launcher --version
# Optionally exercise the tray extra:
pip install "docker-app-launcher[tray]==<new-version>" \
            --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/
deactivate && rm -rf /tmp/testinstall
cd -
```

Patch releases of pure code with no new dependency may skip TestPyPI.
Anything that touches packaging metadata (deps, entry points,
`py.typed`, the `tray` extra, package data) MUST go through TestPyPI.

---

## Step 8: Tag, push, publish to PyPI

```bash
git push origin main
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z

poetry config pypi-token.pypi <token>   # one-time setup
make publish                            # gated build + upload to PyPI
```

A tag push also triggers `.github/workflows/publish.yml` (token-based
upload via `PYPI_TOKEN`). Use either the `make publish` path OR the
tag-triggered workflow — not both for the same version, since PyPI
rejects a duplicate upload.

---

## Step 9: GitHub Release

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes "$(awk '/^## \[X.Y.Z\]/{f=1;next}/^## \[/{f=0}f' CHANGELOG.md)"
```

If `gh` is unavailable, open `…/releases/new`, pick the tag, paste the
CHANGELOG excerpt.

---

## Final checklist

- [ ] Reviewed commits since the last tag
- [ ] Version picked per SemVer and confirmed by the user
- [ ] CHANGELOG entry committed
- [ ] `pyproject.toml` version bumped; `__version__` matches
- [ ] `make release-check` clean
- [ ] `poetry run pre-commit run --all-files` clean
- [ ] Wheel contents inspected (incl. `py.typed`), no surprises
- [ ] TestPyPI publish + smoke install green (non-trivial releases)
- [ ] Git tag created and pushed
- [ ] PyPI page shows new version (via `make publish` or the tag workflow)
- [ ] GitHub release published

---

## Troubleshooting

- **PyPI rejects the upload: version already exists.** PyPI versions
  are immutable. Bump to the next patch and re-run from Step 4. If a
  bad version shipped, *yank* it on PyPI (hides from new resolves,
  does not delete).
- **Wrong version after a tag push (not yet on PyPI):**
  `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`, then
  re-tag correctly.
- **Smoke install fails:** most often a runtime dependency missing
  from `pyproject.toml` (installed in dev env but not declared). Add
  it under `[project] dependencies`, rebuild, retry.
- **Build fails on dependencies:** `poetry lock && poetry install` to
  resync, then rebuild.

---

## Note for AI assistants

This is a guide, not a rigid script. If the user explicitly asks for a
deviation (e.g. "skip TestPyPI this time"), accept it and note why.
But safety items — tests green, build successful, correct version —
must NEVER be skipped, not even on instruction. Better to postpone the
release than to ship broken software.
