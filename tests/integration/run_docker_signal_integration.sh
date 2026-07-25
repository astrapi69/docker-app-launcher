#!/usr/bin/env bash
# Real docker-daemon signal integration (#27): privileged throwaway container,
# REAL dockerd, user without docker-group membership. Both directions:
# daemon up -> permission classification; daemon stopped -> down classification.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

docker run --rm --privileged -v "$REPO_ROOT":/src:ro ubuntu:24.04 bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq > /dev/null
    apt-get install -y -qq docker.io python3-venv > /dev/null 2>&1
    useradd -m daltest    # deliberately NOT in the docker group
    dockerd > /var/log/dockerd.log 2>&1 &
    until docker info > /dev/null 2>&1; do sleep 1; done

    python3 -m venv /venv && /venv/bin/pip install -q /src pytest && chmod -R a+rX /venv

    echo "=== Phase 1: daemon UP, no group ==="
    su daltest -c "DAL_DOCKER_SIGNAL_INTEGRATION=1 /venv/bin/pytest -v -s \
        /src/tests/integration/test_docker_signal_real.py \
        -p no:cacheprovider -o addopts=\"\""

    echo "=== Phase 2: daemon STOPPED ==="
    kill %1; sleep 3
    touch /tmp/dal-daemon-stopped && chmod a+r /tmp/dal-daemon-stopped
    su daltest -c "DAL_DOCKER_SIGNAL_INTEGRATION=1 /venv/bin/pytest -v -s \
        /src/tests/integration/test_docker_signal_real.py::test_stopped_daemon_classifies_as_down \
        -p no:cacheprovider -o addopts=\"\""
'
echo "docker signal integration: OK (container removed, host untouched)"
