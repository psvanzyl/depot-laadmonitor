"""Regression tests for the Depotcharge snapshot ingester.

These pin the rules that matter: scope (NL only), the relevance gate, and the
fact that truck capability comes from the feed's own truckCompatibility field
rather than from a guess about the operator's name.
"""

from __future__ import annotations

from datetime import date

from depotmon.ingest.depotcharge import normalise

FLEET = "https://hub.depotcharge.com/fleet/map?fleetId=x"


def _loc(**over):
    loc = {
        "id": "loc-1",
        "name": "Test Laadplein",
        "organization": {"id": "org", "name": "Bode Scholten"},
        "operationalStatus": "OPERATIONAL",
        "accessType": "PRIVATE",
        "openingDate": "2024-01",
        "coordinates": {"longitude": 4.4931, "latitude": 52.0611},
        "address": {"street": "Fokkerstraat 58", "city": "Zoetermeer",
                    "postalCode": "2722 NG", "country": "Nederland"},
    }
    loc.update(over)
    return {"location": loc, "connectorsCount": 2}


def _cp(kw=360.0, ctype="IEC_62196_T2_COMBO", truck="PULL_THROUGH"):
    return [{"connectors": [{"connectorType": ctype, "maxElectricPower": kw,
                             "truckCompatibility": truck}]}]


def test_nl_private_truck_site_is_operational_and_hgv():
    sig = normalise(_loc(), _cp(), FLEET)
    assert sig is not None
    assert sig["source"] == "depotcharge"
    assert sig["signal_id"] == "depotcharge:loc-1"
    assert sig["hgv_capable"] is True
    assert sig["access"] == "private"
    assert sig["status_hint"] == "operational"
    assert float(sig["power_kw"]) == 360.0
    assert sig["connector_types"] == ["CCS"]
    assert sig["postcode"] == "2722NG"          # spaces stripped
    assert sig["observed_at"] == date.today()
    assert sig["doc_url"] == FLEET


def test_foreign_site_is_kept_and_labelled():
    """The feed is multi-country now: a German site belongs on the map.

    This used to assert the opposite. The map covers DE/CH/ES/IT/AT/LU/LI, so the
    test asserts what ships: kept, with an ISO-2 country for the UI.
    """
    loc = _loc()
    loc["location"]["address"]["country"] = "Deutschland"
    sig = normalise(loc, _cp(), FLEET)
    assert sig is not None
    assert sig["country"] == "DE"


def test_dutch_alias_spelling_is_kept():
    loc = _loc()
    loc["location"]["address"]["country"] = "Niederlande"
    assert normalise(loc, _cp(), FLEET) is not None


def test_planned_public_site_without_connectors_is_dropped():
    """The 49 filler rows from Local Green Energy must not enter the map.

    Still dropped after the multi-country change: the gate now keys on the
    declared connector count, and these rows declare zero.
    """
    loc = _loc(operationalStatus="PLANNED", accessType="PUBLIC")
    loc["connectorsCount"] = 0  # these rows declare no hardware at all
    assert normalise(loc, [], FLEET) is None


def test_public_site_with_truck_connectors_is_kept():
    """A public HDV hub is real evidence even if scoring later penalises it."""
    loc = _loc(accessType="PUBLIC")
    sig = normalise(loc, _cp(kw=400.0), FLEET)
    assert sig is not None
    assert sig["access"] == "public"
    assert sig["status_hint"] == "operational"


def test_not_compatible_connector_is_not_truck_capable():
    loc = _loc(operationalStatus="OPERATIONAL", accessType="PRIVATE")
    sig = normalise(loc, _cp(kw=22.0, truck="NOT_COMPATIBLE"), FLEET)
    assert sig is not None
    assert sig["hgv_capable"] is False
    # 22 kW and no truck rating: operational, but scored as a claim we cannot back.
    assert sig["status_hint"] == "planned"


def test_power_is_max_across_connectors():
    cps = [{"connectors": [
        {"connectorType": "IEC_62196_T2", "maxElectricPower": 22, "truckCompatibility": None},
        {"connectorType": "IEC_62196_T2_COMBO", "maxElectricPower": 240,
         "truckCompatibility": "UNCOUPLE"},
    ]}]
    sig = normalise(_loc(), cps, FLEET)
    assert float(sig["power_kw"]) == 240.0
    assert sig["n_points"] == 2
    assert sig["connector_types"] == ["CCS", "TYPE2"]
    assert sig["hgv_capable"] is True


def test_operational_flag_without_hardware_is_not_operational():
    """A site claiming OPERATIONAL with no connectors and no power is a plan."""
    sig = normalise(_loc(), [], FLEET)
    assert sig is not None
    assert sig["status_hint"] == "planned"
    assert sig["power_kw"] is None


def test_missing_coordinates_is_skipped():
    loc = _loc()
    loc["location"]["coordinates"] = {}
    assert normalise(loc, _cp(), FLEET) is None


def test_mcs_connector_maps_to_mcs():
    sig = normalise(_loc(), _cp(kw=1200.0, ctype="MCS"), FLEET)
    assert sig["connector_types"] == ["MCS"]


def test_content_hash_is_stable_and_sensitive():
    a = normalise(_loc(), _cp(), FLEET)
    b = normalise(_loc(), _cp(), FLEET)
    c = normalise(_loc(), _cp(kw=240.0), FLEET)
    assert a["content_hash"] == b["content_hash"]
    assert a["content_hash"] != c["content_hash"]
