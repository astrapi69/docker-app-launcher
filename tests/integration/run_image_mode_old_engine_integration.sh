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

# Resource measurement BEFORE the engine start (#109). The previous
# classification ("both attempts down = a real finding") did NOT hold: the
# SAME tree was red at 12:33 and green at 12:37, both attempts down each
# time, with the dind entrypoint reporting "sed: write error" - a WRITE
# failure, which points at an exhausted runner rather than at the engine
# generation this cell is supposed to measure. An unclassified red here is
# worse than a red: a job that is intermittently red in the release-blocking
# set teaches people to re-run until green, and then the assurance is worth
# nothing without anyone noticing. So the resources are measured up front,
# and a failure says WHICH of three verdicts it reached.
DOCKER_ROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
free_kb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }
free_inodes() { df -Pi "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }
# Below this the runner is treated as exhausted rather than as a measurement
# of the engine generation. 2 GiB: the pinned dind image alone unpacks to
# ~300 MB and the cell pulls a probe image on top.
MIN_FREE_KB=2097152
MIN_FREE_INODES=50000

report_resources() {
  echo "--- runner resources ($1) ---"
  df -Ph "$DOCKER_ROOT" / 2>/dev/null || true
  echo "free inodes on $DOCKER_ROOT: $(free_inodes "$DOCKER_ROOT")"
  docker system df 2>/dev/null || true
}

resources_exhausted() {
  local kb inodes
  kb="$(free_kb "$DOCKER_ROOT")"
  inodes="$(free_inodes "$DOCKER_ROOT")"
  # Fail CLOSED on an unmeasurable basis: if df says nothing, do not claim
  # "plenty of room" - that would be the swallowed-probe class again.
  [ -z "$kb" ] && return 0
  [ "$kb" -lt "$MIN_FREE_KB" ] && return 0
  [ -n "$inodes" ] && [ "$inodes" != "-" ] && [ "$inodes" -lt "$MIN_FREE_INODES" ] && return 0
  return 1
}

report_resources "before start"
echo "=== starting pinned old engine: docker:${ENGINE_TAG} ==="
# CAUSE-LEVEL stabilization (#93 companion): the dind daemon can die right
# after start on shared runners. The original wait loop fell through after
# 60 tries WITHOUT failing, so the next exec hit a dead engine with an
# opaque "container is not running". Hence: capability-checked readiness
# (docker version over the socket), a hard aliveness check, ONE bounded
# restart attempt, and the daemon's own log on every failed attempt.
#
# The three verdicts this cell can reach, named in its own output (#109):
#   ENGINE-NEVER-STARTED / INFRASTRUCTURE - the engine did not come up AND
#     the runner is out of space or inodes. Exit 2. NOT a statement about
#     the engine generation: the cell measured nothing. Still red, because
#     "could not check" must never read as "nothing to find".
#   ENGINE-NEVER-STARTED / UNDIAGNOSED - it did not come up with resources
#     to spare. Exit 1. Needs a human; the daemon log is dumped in full.
#   FINDING - the engine WAS ready and the cell's tests failed. Exit 1.
#     This, and only this, is a statement about the minimum engine
#     generation the image mode promises.
start_engine() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --privileged --name "$NAME" -e DOCKER_TLS_CERTDIR= \
    "docker:${ENGINE_TAG}" dockerd --host=tcp://0.0.0.0:2375 --host=unix:///var/run/docker.sock --tls=false >/dev/null
  for _ in $(seq 1 60); do
    if docker exec "$NAME" docker version >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}
for attempt in 1 2; do
  if start_engine && [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = "true" ]; then
    break
  fi
  echo "WARN: dind engine did not come up (attempt $attempt) - daemon log:"
  docker logs "$NAME" 2>&1 | tail -40
  echo "container exit code: $(docker inspect -f '{{.State.ExitCode}}' "$NAME" 2>/dev/null || echo unknown)"
  report_resources "after failed attempt $attempt"
  if [ "$attempt" = "2" ]; then
    if resources_exhausted; then
      echo "VERDICT: ENGINE-NEVER-STARTED / INFRASTRUCTURE - the runner is out of"
      echo "  disk space or inodes (see the measurement above), so this run measured"
      echo "  NOTHING about the engine generation. Do not read it as a finding, and"
      echo "  do not re-run until green without looking: see #109."
      exit 2
    fi
    echo "VERDICT: ENGINE-NEVER-STARTED / UNDIAGNOSED - resources were sufficient,"
    echo "  so the cause is unknown. Full daemon log above; this needs a human (#109)."
    exit 1
  fi
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
# From here on the engine IS ready, so anything that fails below is a
# statement about the engine generation - the only verdict this cell is
# allowed to make about the image mode's promise (#109).
if DAL_IMAGE_OLD_ENGINE=1 \
  DAL_OLD_ENGINE_HTTP_HOST="$IP" \
  DAL_OLD_ENGINE_EXPECT="$(echo "$ENGINE_TAG" | grep -oE '^[0-9]+\.[0-9]+\.')" \
  DOCKER_HOST="tcp://${IP}:2375" \
  poetry run pytest tests/integration/test_image_mode_old_engine_real.py -v "$@"; then
  echo "VERDICT: PASS - the image mode works on docker:${ENGINE_TAG} (engine was ready, cell green)"
else
  status=$?
  echo "VERDICT: FINDING - the engine was READY and the cell failed, so this IS a"
  echo "  statement about the minimum engine generation the image mode promises."
  echo "  Never bend the cell to make it pass; the promise is what is wrong."
  exit "$status"
fi
