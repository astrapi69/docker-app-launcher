#!/usr/bin/env bash
# Real environment-matrix integration run (see docs/environment-matrix.md).
# Each cell is a throwaway --privileged container with a REAL nested dockerd
# (or none), the package installed into a venv, and one gated scenario from
# tests/integration/test_env_matrix_real.py. The host is mounted read-only and
# --rm removes every trace. NOT part of `make ci`: it needs a real Docker
# daemon and --privileged, exactly like run_docker_signal_integration.sh.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

run_cell() {
    local cell="$1"; shift
    local provision="$1"; shift
    local as_user="$1"; shift  # "root" or "daltest"
    echo "=== env-matrix cell: ${cell} (as ${as_user}) ==="
    docker run --rm --privileged -v "$REPO_ROOT":/src:ro ubuntu:24.04 bash -euo pipefail -c "
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq > /dev/null
        ${provision}
        python3 -m venv /venv && /venv/bin/pip install -q /src pytest && chmod -R a+rX /venv
        RUN='DAL_ENV_MATRIX_INTEGRATION=1 DAL_ENV_MATRIX_SCENARIO=${cell} /venv/bin/pytest -v -s \
            /src/tests/integration/test_env_matrix_real.py -p no:cacheprovider -o addopts=\"\"'
        if [ '${as_user}' = 'root' ]; then eval \"\$RUN\"; else su daltest -c \"\$RUN\"; fi
    "
}

# Cell: engine present (docker.io ships NO compose plugin and NO v1), daemon up,
# run as root so Docker is usable -> the #48 ladder must be actionable pre-build.
run_cell no_compose '
    apt-get install -y -qq docker.io python3-venv > /dev/null 2>&1
    dockerd > /var/log/dockerd.log 2>&1 &
    until docker info > /dev/null 2>&1; do sleep 1; done
' root

# Cell: no docker binary at all -> a clear not-installed verdict.
run_cell no_docker '
    apt-get install -y -qq python3-venv > /dev/null 2>&1
' root

# Cell: daemon up, user NOT in the docker group -> the ONE cell where
# "usermod -aG docker" is correct advice (a local root unix socket).
run_cell no_group '
    apt-get install -y -qq docker.io python3-venv > /dev/null 2>&1
    useradd -m daltest
    dockerd > /var/log/dockerd.log 2>&1 &
    until docker info > /dev/null 2>&1; do sleep 1; done
' daltest

echo "env matrix integration: OK (containers removed, host untouched)"
