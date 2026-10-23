"""Regression tests: public / semi-public HDV charge points on the map.

These pin the fixes that made NDW visible at all:

  * NDW ingests as kind="chargepoint", not "ndw" — keying on "ndw" made the whole
    counter-evidence mechanism dead code;
  * a public point is evidence when it can serve a truck, and counter-evidence only
    when it cannot;
  * a 150 kW point must not cancel the 300 kW point next to it;
  * charge-point registries carry no city, so premises need a coordinate key.
"""

from __future__ import annotations

from depotmon.classify.premises import (
    COUNTER,
    HDV_PROXY_KW,
    _access_class,
    _hdv_evidence,
    _key,
    score_evidence,
)


def item(**over):
    base = {
        "kind": "chargepoint",
        "source": "ndw",
        "operator": "Fastned",
        "city": None,
        "postcode": None,
        "address": None,
        "power_kw": 400.0,
        "n_points": 2,
        "hgv_capable": False,
        "access": "unknown",
        "status_hint": "operational",
        "doc_url": "https://opendata.ndw.nu/charging_point",
        "observed_at": None,
        "raw_state": {},
        "text_excerpt": None,
        "lat": 52.1,
        "lon": 4.3,
    }
    base.update(over)
    return base


# --- access classification (RVO terminology) ---------------------------------

def test_chargepoint_is_public():
    assert _access_class([item()]) == "public"


def test_customers_access_is_semi_public():
    assert _access_class([item(kind="osm", access="customers")]) == "semi_public"


def test_private_access_is_private():
    assert _access_class([item(kind="depotcharge", access="private")]) == "private"


def test_unknown_when_nothing_says():
    assert _access_class([item(kind="news", access="unknown")]) == "unknown"


# --- HDV detection -----------------------------------------------------------

def test_explicit_truck_rating_counts():
    assert _hdv_evidence(item(power_kw=22.0, hgv_capable=True)) is True


def test_power_threshold_boundary():
    assert _hdv_evidence(item(power_kw=HDV_PROXY_KW)) is True
    assert _hdv_evidence(item(power_kw=HDV_PROXY_KW - 1)) is False


# --- scoring -----------------------------------------------------------------

def test_public_hdv_point_scores_instead_of_being_countered():
    """A 400 kW public point with no private claim is a site, not counter-evidence."""
    verdict = score_evidence([item(power_kw=400.0)])
    assert verdict["tier"] == "C"
    assert verdict["score"] > 0.4
    assert not any(r.startswith("counter:") for r in verdict["reasons"])


def test_public_car_charger_alone_is_not_published():
    verdict = score_evidence([item(power_kw=22.0)])
    assert verdict["tier"] is None


def test_low_power_point_does_not_cancel_its_neighbour():
    """The bug that killed whole hubs: 150 kW scored as counter to the 300 kW bay."""
    verdict = score_evidence([item(power_kw=400.0), item(power_kw=150.0)])
    assert verdict["tier"] is not None
    assert not any("charge-point signal" in r for r in verdict["reasons"])


def test_public_point_still_contradicts_a_private_depot_claim():
    verdict = score_evidence([
        item(kind="news", source="news", power_kw=None, access="unknown",
             status_hint="opening", postcode="6718VD"),
        item(power_kw=22.0),
    ])
    assert any("counter" in r for r in verdict["reasons"])
    assert verdict["score"] < 0.5


def test_public_access_penalty_skipped_for_hdv():
    verdict = score_evidence([item(power_kw=400.0, access="public")])
    assert not any("public laadplein" in r for r in verdict["reasons"])


def test_ndw_kind_is_registered_as_counter():
    """If this ever fails, NDW is invisible again."""
    assert "chargepoint" in COUNTER
    assert "ndw" in COUNTER


# --- premises keys -----------------------------------------------------------

def test_coordinate_place_key_keeps_points_apart():
    a = _key(item(lat=52.1001, lon=4.3001))
    b = _key(item(lat=53.2002, lon=5.4002))
    assert a != b
    assert a[1] and b[1]


def test_points_without_place_and_without_coords_collapse():
    """Documents the failure mode the coordinate key exists to prevent."""
    a = _key(item(lat=None, lon=None, operator="Fastned"))
    b = _key(item(lat=None, lon=None, operator="Fastned"))
    assert a == b


# --- province from coordinates ----------------------------------------------

def test_province_lookup_uses_lon_lat_not_rd():
    import importlib.util as u
    from pathlib import Path

    spec = u.spec_from_file_location(
        "export_webapp_data", Path(__file__).resolve().parents[1] / "scripts/export_webapp_data.py"
    )
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.province_for(4.895, 52.37) == "Noord-Holland"   # Amsterdam
    assert m.province_for(5.61, 52.05) == "Gelderland"       # Ede
    assert m.province_for(6.57, 53.22) == "Groningen"
