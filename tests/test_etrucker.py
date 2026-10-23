"""Tests for the eTrucker snapshot ingester.

These pin the rule the whole map depends on: a site is only HGV-capable when the
feed says so (a heavy_vehicles flag, an N2/N3 restriction code, or an MCS
connector) — never because the name sounds like a truck stop.
"""

from __future__ import annotations

from depotmon.ingest.etrucker import normalise

URL = "https://etrucker.io"


def _row(**over):
    row = {
        "id": "etr-1",
        "name": "Truckstop A1",
        "operator": "Fastned",
        "address": "Rijksweg 1",
        "postcode": "1234AB",
        "city": "Utrecht",
        "country": "NLD",
        "lat": 52.0912,
        "lon": 5.1201,
        "max_kw": 400.0,
        "n_points": 6,
        "connectors": ["CCS", "MCS"],
        "access": "public",
        "location_type": "ALONG_MOTORWAY",
        "hgv_flags": {"heavy_vehicles_dedicated": True},
        "hgv_restrictions": [],
        "last_updated": "2026-09-01T10:00:00Z",
    }
    row.update(over)
    return row


def test_dedicated_bay_is_hgv_and_operational():
    sig = normalise(_row(), URL)
    assert sig is not None
    assert sig["source"] == "etrucker"
    assert sig["kind"] == "etrucker"
    assert sig["signal_id"] == "etrucker:etr-1"
    assert sig["hgv_capable"] is True
    assert sig["access"] == "public"
    assert sig["status_hint"] == "operational"
    assert sig["power_kw"] == 400.0
    assert sig["connector_types"] == ["CCS", "MCS"]
    assert sig["doc_url"] == URL


def test_no_truck_evidence_is_dropped():
    sig = normalise(_row(hgv_flags={}, connectors=["CCS"], hgv_restrictions=[]), URL)
    assert sig is None


def test_n2_restriction_counts_as_truck_evidence():
    sig = normalise(_row(hgv_flags={}, connectors=["CCS"], hgv_restrictions=["N2"]), URL)
    assert sig is not None
    assert sig["hgv_capable"] is True


def test_mcs_connector_counts_as_truck_evidence():
    sig = normalise(_row(hgv_flags={}, connectors=["MCS"]), URL)
    assert sig is not None
    assert sig["hgv_capable"] is True


def test_missing_coordinates_is_dropped():
    assert normalise(_row(lat=None), URL) is None
    assert normalise(_row(lon=None), URL) is None


def test_without_last_updated_status_is_unknown():
    sig = normalise(_row(last_updated=None), URL)
    assert sig is not None
    assert sig["status_hint"] == "unknown"


def test_power_and_points_pass_through():
    sig = normalise(_row(max_kw=150.0, n_points=2), URL)
    assert sig["power_kw"] == 150.0
    assert sig["n_points"] == 2


def test_content_hash_is_stable_and_not_empty():
    a = normalise(_row(), URL)["content_hash"]
    b = normalise(_row(), URL)["content_hash"]
    c = normalise(_row(max_kw=150.0), URL)["content_hash"]
    assert a == b
    assert a != c
