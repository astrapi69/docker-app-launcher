#!/usr/bin/env bash
# Real-pkexec integration run: throwaway Ubuntu container, NEVER the host.
#
# The container gets polkitd + a narrow rule (usermod-only, daltest-only),
# installs the package from the read-only mounted source, and runs the
# integration test as the dedicated 'daltest' user - the real pkexec path,
# auto-approved by the rule instead of an interactive dialog. --rm removes
# every trace afterwards; the host system is mounted read-only and untouched.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

docker run --rm -v "$REPO_ROOT":/src:ro ubuntu:24.04 bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq > /dev/null
    apt-get install -y -qq polkitd pkexec dbus python3-venv > /dev/null 2>&1

    useradd -m daltest
    groupadd docker

    mkdir -p /run/dbus && dbus-daemon --system --fork
    /usr/lib/polkit-1/polkitd --no-debug > /dev/null 2>&1 &
    cp /src/tests/integration/49-dal-test.rules /etc/polkit-1/rules.d/
    sleep 1

    python3 -m venv /venv
    /venv/bin/pip install -q /src pytest
    chmod -R a+rX /venv

    su daltest -c "DAL_PKEXEC_INTEGRATION=1 /venv/bin/pytest \
        /src/tests/integration/test_pkexec_real.py -v \
        -p no:cacheprovider -o addopts=\"\""
'
echo "pkexec integration: OK (container removed, host untouched)"
