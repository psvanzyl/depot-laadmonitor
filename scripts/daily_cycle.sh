#!/usr/bin/env bash
# Scheduled daily cycle: pipeline -> evidence gates -> refresh the map data.
#
# Order matters and is not negotiable:
#   1. depotmon daily   ingest, resolve, classify, score, diff
#   2. purge_junk       delete headline fragments / implausible cities / non-logistics news
#   3. attach + snap    put a finding on the company's own yard where provable
#   4. score again      scores must be computed AFTER the gates, never before
#   5. export_map       write the GeoJSON the webapp serves
#
# ExecStartPost is skipped by systemd when ExecStart fails, so a flaky source
# (Overpass mirrors time out regularly) used to leave depot.home.arpa serving
# stale data. The gates and the export therefore run regardless, and the
# pipeline's exit code is still propagated so systemd reports the run honestly.
set -uo pipefail
cd /root/projects/depot-laadmonitor

pipeline_rc=0

scripts/run.sh -m depotmon.cli daily || pipeline_rc=$?
echo "[daily] pipeline exit: ${pipeline_rc}"

# --- evidence gates (must precede the final score) ---
scripts/run.sh scripts/purge_junk.py        || echo "[daily] WARN purge_junk failed"
scripts/run.sh scripts/attach_companies.py  || echo "[daily] WARN attach failed"
scripts/run.sh scripts/snap_companies.py    || echo "[daily] WARN snap failed"
scripts/run.sh -m depotmon.cli score        || echo "[daily] WARN rescore failed"

# --- publish ---
export_rc=0
scripts/export_map.sh || export_rc=$?
echo "[daily] export exit: ${export_rc}"

if [ "${pipeline_rc}" -ne 0 ]; then
  exit "${pipeline_rc}"
fi
exit "${export_rc}"
