#!/usr/bin/env bash
# Refresh the static map data from the live DB.
# Keeps depot.home.arpa in sync with the database after an ingest cycle.
set -euo pipefail
cd /root/projects/depot-laadmonitor

# 1. company base layer (maplibre geojson)
scripts/run.sh scripts/export_companies.py

# 2. scored depots + summary
scripts/run.sh scripts/export_webapp_data.py

echo "export done: $(date -Is)"
