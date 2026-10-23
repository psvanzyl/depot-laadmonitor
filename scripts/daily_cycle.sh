#!/usr/bin/env bash
# Scheduled daily cycle: pipeline -> evidence gates -> refresh the map data.
#
# Order matters and is not negotiable:
#   1. depotmon daily   ingest, resolve, classify, score, diff
#   2. purge_junk       delete headline fragments / implausible cities / parking bays
#   3. attach + snap    put a finding on the company's own yard where provable
#   4. score again      scores must be computed AFTER the gates, never before
#   5. export_map       write the GeoJSON the webapp serves
#
# ExecStartPost is skipped by systemd when ExecStart fails, so a flaky source
# (Overpass mirrors time out regularly) used to leave depot.home.arpa serving
# stale data. The gates and the export therefore run regardless.
#
# EXIT CODE CONTRACT
#   The CLI returns 2 for "degraded": one or more remote sources failed but the
#   run completed and the database is consistent (it logs ALERT [source_failure]).
#   It returns 1 for a hard failure (crash, DB error) where nothing should be
#   trusted. Publishing is what matters for this product, so:
#     0  map refreshed, nothing to do
#     0  map refreshed, sources degraded (logged loudly, status file records it)
#     rc export failed, or the pipeline hard-failed
#   A red unit every morning for a source that is flaky by nature trains us to
#   ignore red, which is worse than an accurate warning.
set -uo pipefail
cd /root/projects/depot-laadmonitor

STATUS_FILE=/var/log/depotmon-status.json
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

degraded=false
[ "${pipeline_rc}" -eq 2 ] && degraded=true

printf '{"finished":"%s","pipeline_rc":%d,"export_rc":%d,"degraded":%s}\n' \
  "$(date -Is)" "${pipeline_rc}" "${export_rc}" "${degraded}" > "${STATUS_FILE}"

if [ "${export_rc}" -ne 0 ]; then
  echo "[daily] FAIL publish failed (export_rc=${export_rc})"
  exit "${export_rc}"
fi
if [ "${pipeline_rc}" -ne 0 ] && [ "${pipeline_rc}" -ne 2 ]; then
  echo "[daily] FAIL pipeline hard-failed (rc=${pipeline_rc}); map kept, data suspect"
  exit "${pipeline_rc}"
fi
if [ "${degraded}" = true ]; then
  echo "[daily] DEGRADED source failure, map still refreshed (see ALERT above)"
fi
echo "[daily] OK"
exit 0
