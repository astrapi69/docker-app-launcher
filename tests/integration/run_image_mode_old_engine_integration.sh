#!/usr/bin/env bash
# Old-engine cell for the image mode (#84): a PINNED 20.10-class engine,
# proven free of compose/buildx, then both acquisition sources measured.
#
# Usage: tests/integration/run_image_mode_old_engine_integration.sh
#   DAL_OLD_ENGINE_TAG overrides the pinned dind tag (default 20.10.24-dind).
#
# Needs a real local Docker daemon (to host the dind container) and network
# access (the dind engine pulls the probe image from the registry).
set -euo pipefail

ENGINE_TAG="${DAL_OLD_ENGINE_TAG:-20.10.24-dind}"
NAME="dal-old-engine"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "=== starting pinned old engine: docker:${ENGINE_TAG} ==="
docker run -d --privileged --name "$NAME" -e DOCKER_TLS_CERTDIR= \
  "docker:${ENGINE_TAG}" dockerd --host=tcp://0.0.0.0:2375 --host=unix:///var/run/docker.sock --tls=false >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$NAME" docker version >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$NAME" docker version --format 'engine ready: {{.Server.Version}} (API {{.Server.APIVersion}})'

echo "=== provisioning the distro profile (engine only, no client plugins) ==="
# The dind CONVENIENCE image bundles compose+buildx client plugins
# (/usr/local/libexec/docker/cli-plugins) - the distro docker.io profile this
# cell models does not. Removing them touches only the CLI side; the ENGINE
# is exactly the pinned 20.10 daemon. The absence proof below then gates.
docker exec "$NAME" rm -rf /usr/local/libexec/docker/cli-plugins /usr/libexec/docker/cli-plugins

echo "=== proving toolchain ABSENCE (the point of the cell) ==="
if docker exec "$NAME" docker compose version >/dev/null 2>&1; then
  echo "FAIL: a compose plugin is present in docker:${ENGINE_TAG} - the cell proves nothing"; exit 1
fi
if docker exec "$NAME" docker buildx version >/dev/null 2>&1; then
  echo "FAIL: buildx is present in docker:${ENGINE_TAG} - the cell proves nothing"; exit 1
fi
if docker exec "$NAME" sh -c 'command -v docker-compose' >/dev/null 2>&1; then
  echo "FAIL: legacy docker-compose v1 is present - the cell proves nothing"; exit 1
fi
echo "proven: no compose plugin, no legacy docker-compose, no buildx in docker:${ENGINE_TAG}"

IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$NAME")
echo "=== running the cell against tcp://${IP}:2375 ==="
DAL_IMAGE_OLD_ENGINE=1 \
DAL_OLD_ENGINE_HTTP_HOST="$IP" \
DAL_OLD_ENGINE_EXPECT="$(echo "$ENGINE_TAG" | grep -oE '^[0-9]+\.[0-9]+\.')" \
DOCKER_HOST="tcp://${IP}:2375" \
  poetry run pytest tests/integration/test_image_mode_old_engine_real.py -v "$@"
