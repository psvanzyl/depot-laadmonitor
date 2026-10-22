"""Unit tests for ingester normalisation and permit query building."""

import json

from depotmon.ingest import ndw, osm, permits
from datetime import date


def test_ndw_normalise_basic():
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [4.9, 52.3]},
        "properties": {
            "id": "NL-NDW-1",
            "address": "Zonnebaan 12",
            "city": "Utrecht",
            "operator_name": "Fastned",
            "open": True,
            "availabilities": [
                {"power_max": 400000, "connector_type": "IEC_62196_T2_COMBO", "total": 2},
                {"power_max": 400000, "connector_type": "IEC_62196_T2", "total": 1},
            ],
        },
    }
    sig = ndw.normalise(feature)
    assert sig["signal_id"] == "ndw:NL-NDW-1"
    assert sig["power_kw"] == 400.0
    assert sig["n_points"] == 3
    assert sig["hgv_capable"] is True
    assert sig["status_hint"] == "operational"
    assert "IEC_62196_T2_COMBO" in sig["connector_types"]


def test_ndw_rejects_impossible_power():
    feature = {
        "geometry": {"type": "Point", "coordinates": [4.9, 52.3]},
        "properties": {
            "id": "X",
            "availabilities": [{"power_max": 120000000}],
        },
    }
    assert ndw.normalise(feature) is None


def test_ndw_missing_geometry():
    assert ndw.normalise({"properties": {"id": "X"}, "geometry": None}) is None


def test_osm_normalise_private_hgv():
    el = {
        "type": "node",
        "id": 12345,
        "lat": 52.1,
        "lon": 4.5,
        "tags": {
            "amenity": "charging_station",
            "hgv": "yes",
            "access": "private",
            "operator": "Logistiek BV",
            "socket:mcs": "2",
            "charging_station:output": "1.2 MW",
            "capacity": "4",
            "addr:street": "Havenweg",
            "addr:housenumber": "5",
        },
    }
    sig = osm.normalise(el)
    assert sig["signal_id"] == "osm:node/12345"
    assert sig["power_kw"] == 1200.0
    assert sig["access"] == "private"
    assert sig["hgv_capable"] is True
    assert "MCS" in sig["connector_types"]
    assert sig["n_points"] == 4
    assert sig["address"] == "Havenweg 5"


def test_osm_way_uses_center():
    el = {"type": "way", "id": 9, "center": {"lat": 52.0, "lon": 4.0},
          "tags": {"amenity": "charging_station", "access": "customers"}}
    sig = osm.normalise(el)
    assert sig["lat"] == 52.0 and sig["access"] == "customers"


def test_permit_query_contains_window_and_terms():
    q = permits.build_query(date(2026, 10, 1))
    assert 'c.product-area=="officielepublicaties"' in q
    assert "dt.modified>=2026-10-01" in q
    assert "laadinfrastructuur" in q


def _record(title, doc_type="Omgevingsvergunning"):
    return {
        "fields": {
            "title": title,
            "type": doc_type,
            "identifier": "stcrt-2026-12345",
            "modified": "2026-10-05T09:00:00Z",
            "creator": "Gemeente Utrecht",
            "content-area": "officielepublicaties/gmb",
        },
        "xml_url": "https://example.invalid/doc.xml",
    }


def test_permit_normalise_relevant_notice():
    body = (
        "Aanvraag omgevingsvergunning. Aanvrager: De Vries Transport B.V. "
        "Locatie: Zonnebaan 12, 3542 EA Utrecht. Het betreft het realiseren van een "
        "laadplein met 8 snelladers (1,2 MW) voor vrachtwagens."
    )
    sig = permits.normalise_document(_record("Laadplein Zonnebaan"), body,
                                     "https://example.invalid/doc.xml")
    assert sig is not None
    assert sig["power_kw"] == 1200.0
    assert sig["operator"].startswith("De Vries")
    assert sig["status_hint"] == "permit_requested"
    assert sig["hgv_capable"] is True
    assert sig["address"] and "Zonnebaan" in sig["address"]
    json.dumps(sig["raw_state"])  # must be JSON-serialisable


def test_permit_normalise_drops_irrelevant():
    body = "Kennisgeving verlengen beslistermijn voor een dakkapel."
    assert permits.normalise_document(_record("Dakkapel"), body, "u") is None


def test_permit_normalise_drops_policy_without_address():
    body = "Beleidsregel laadinfrastructuur vrachtwagens: de gemeente stimuleert snelladers."
    assert permits.normalise_document(_record("Beleidsregel", "Beleidsregel"), body, "u") is None
