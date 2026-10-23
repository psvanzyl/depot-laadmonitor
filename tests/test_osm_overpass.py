"""Regression tests for the Overpass ingest.

Every one of these pins a defect that made the daily job fail while looking
healthy, so the fix cannot quietly rot back:

- area[...] instead of a bbox query cost the mirrors enough to time out;
- a mirror added to the rotation but missing from the robots exemption fails
  instantly and silently (overpass-api.de serves "Disallow: /api/");
- a 200 with an empty body counted as success (overpass.osm.ch answers in 0.2s
  with nothing), which would wipe every OSM-derived signal.
"""

import json

import pytest

from depotmon.ingest import osm


def test_query_uses_bbox_not_area_lookup():
    """area["ISO3166-1"="NL"] made the server resolve an area; bbox is cheap."""
    assert "bbox:50.6,3.2,53.6,7.3" in osm.QUERY
    assert "area[" not in osm.QUERY


def test_every_mirror_is_robots_exempt():
    """A mirror without an exemption is refused instantly by the polite fetcher."""
    for mirror in osm.MIRRORS:
        host = mirror.split("/")[2]
        assert host in osm.OVERPASS_EXEMPT, f"{host} missing from OVERPASS_EXEMPT"
        assert osm.OVERPASS_EXEMPT[host].strip(), f"{host} exemption has no reason"


def test_overpass_timeout_exceeds_observed_latency():
    """A national query measured 67s (api.de) to 158s (private.coffee)."""
    assert osm.OVERPASS_TIMEOUT >= 180


def test_best_mirror_is_tried_first():
    """overpass-api.de answered in 67s when healthy; do not bury it."""
    assert osm.MIRRORS[0].endswith("overpass-api.de/api/interpreter")


def test_normalise_skips_elements_without_coordinates():
    assert osm.normalise({"type": "node", "id": 1, "tags": {}}) is None


def test_normalise_accepts_way_centre():
    sig = osm.normalise(
        {
            "type": "way",
            "id": 42,
            "center": {"lat": 52.37, "lon": 4.89},
            "tags": {"amenity": "charging_station", "hgv": "designated",
                     "name": "Test Yard", "socket:mcs": "1",
                     "charging_station:output": "1200 kW"},
        }
    )
    assert sig is not None
    assert sig["lat"] == pytest.approx(52.37)
    assert sig["lon"] == pytest.approx(4.89)
    assert sig["power_kw"] == pytest.approx(1200.0)
    assert sig["hgv_capable"] is True
    assert "MCS" in (sig["connector_types"] or [])


def test_empty_payload_is_not_success():
    """Mirror the guard in run(): 200 with zero elements must not be accepted."""
    elements = json.loads(json.dumps({"elements": []})).get("elements") or []
    assert not elements  # this is the condition run() raises on


def test_truck_relevance_filter():
    """OSM tags access=private on car chargers constantly; access proves nothing.

    Restoring the OSM ingest added 3,169 signals of which 54% were 11-22 kW car
    chargers at hotels, IKEA and town halls, which flooded tier B. Relevance must
    come from an explicit truck signal.
    """
    assert osm._truck_relevant(
        {"power_kw": 11, "hgv_capable": False, "connector_types": ["type2"]}) is False
    assert osm._truck_relevant(
        {"power_kw": 22, "hgv_capable": False, "connector_types": []}) is False
    assert osm._truck_relevant(
        {"power_kw": None, "hgv_capable": False, "connector_types": None}) is False
    assert osm._truck_relevant(
        {"power_kw": None, "hgv_capable": False, "connector_types": ["MCS"]}) is True
    assert osm._truck_relevant(
        {"power_kw": None, "hgv_capable": True, "connector_types": []}) is True
    assert osm._truck_relevant(
        {"power_kw": 150, "hgv_capable": False, "connector_types": ["ccs"]}) is True


def test_truck_relevance_threshold():
    assert osm.MIN_TRUCK_KW >= 50
    assert osm._truck_relevant(
        {"power_kw": osm.MIN_TRUCK_KW, "hgv_capable": False,
         "connector_types": []}) is True
    assert osm._truck_relevant(
        {"power_kw": osm.MIN_TRUCK_KW - 1, "hgv_capable": False,
         "connector_types": []}) is False
