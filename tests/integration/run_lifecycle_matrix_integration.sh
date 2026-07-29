#!/usr/bin/env bash
# Lifecycle matrix (#79): the full operation set per deployment mode against
# the REAL local daemon - install, install-again, logs, stop, restart of the
# stopped stack, uninstall, and the nothing-runs transitions.
#
# Usage: tests/integration/run_lifecycle_matrix_integration.sh [pytest args]
#   DAL_LIFECYCLE_MATRIX_MODE=image|dockerfile|compose narrows to one mode.
#
# Needs: a running local Docker daemon; for compose mode a usable compose v2
# plugin and buildx (the build modes' normal requirements). Runtime split:
# per-push CI runs the unit suite + the old-engine cell; this full matrix
# runs nightly (lifecycle-matrix.yml) and on demand.
set -euo pipefail

if ! docker info >/dev/null 2>&1; then
  echo "FAIL: no reachable local Docker daemon - the matrix needs one"; exit 1
fi
if [ "${DAL_LIFECYCLE_MATRIX_MODE:-}" != "image" ] && [ "${DAL_LIFECYCLE_MATRIX_MODE:-}" != "dockerfile" ]; then
  if ! docker compose version >/dev/null 2>&1; then
    echo "FAIL: compose mode is part of this run but no compose v2 plugin is usable"
    echo "      (narrow with DAL_LIFECYCLE_MATRIX_MODE=image or =dockerfile)"; exit 1
  fi
fi

DAL_LIFECYCLE_MATRIX=1 poetry run pytest tests/integration/test_lifecycle_matrix_real.py -v "$@"
