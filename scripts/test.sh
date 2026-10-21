#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p work
cleanup() {
  docker compose -f compose.test.yml logs --no-color > work/test-containers.log 2>&1 || true
  docker compose -f compose.test.yml down --remove-orphans
}
trap cleanup EXIT
docker compose -f compose.test.yml up --build -d --wait api
docker compose -f compose.test.yml --profile test build tests
docker compose -f compose.test.yml --profile test run --rm --no-deps tests
docker compose -f compose.test.yml logs --no-color --no-log-prefix api > work/test-api.log
python3 scripts/check_logs.py work/test-api.log
