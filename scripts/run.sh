#!/usr/bin/env bash
# Run a script with the live DB URL (no password on disk).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/env.sh
exec .venv/bin/python "$@"
