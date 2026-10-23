#!/usr/bin/env bash
# Source me: `. scripts/env.sh`
# Builds DATABASE_URL from the live container, so no password is stored on disk
# and nothing is echoed.
set -u
PW="$(docker exec depotmon-db printenv POSTGRES_PASSWORD 2>/dev/null)" || {
  echo "env.sh: depotmon-db not running" >&2
  return 1 2>/dev/null || exit 1
}
[ -n "${PW:-}" ] || { echo "env.sh: empty POSTGRES_PASSWORD" >&2; return 1 2>/dev/null || exit 1; }
export DATABASE_URL="postgresql://depotmon:${PW}@127.0.0.1:5433/depotmon"
unset PW

# Depotcharge snapshot lives in the project tree (data/ is gitignored).
export DEPOTCHARGE_DIR="${DEPOTCHARGE_DIR:-/root/projects/depot-laadmonitor/data/depotcharge}"
export ETRUCKER_DIR="${ETRUCKER_DIR:-/root/projects/depot-laadmonitor/data/etrucker}"
