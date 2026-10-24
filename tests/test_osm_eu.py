"""Tests for the Europe-wide OSM ingests and the country resolution.

These cover the parts that can silently corrupt the map:

* the country lookup must use real polygons. A bounding box labelled Aachen
  (a German city) as BE, because the point is inside the Belgian rectangle
  too. That is a wrong country on the map, so the polygons are tested
  against real cities;
* the ISO-2/ISO-3 normalisation must not turn an unknown code into a valid
  country;
* the truck gate must reject a private car charger and keep an MCS socket.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depotmon.ingest import osm_eu
from depotmon.ingest.country_geom import country_for, is_available
from depotmon.ingest.osm_eu import ISO2_TO_ISO3, EUROPE_BOXES, country_of, normalise

# Real cities, with the country they are actually in. The border cases are the
# ones that broke the bounding-box version.
CITY_COUNTRY = [
    ("Aachen", 6.08, 50.78, "DE"),
    ("Enschede", 6.90, 52.22, "NL"),
    ("Brussels", 4.35, 50.85, "BE"),
    ("Luxembourg city", 6.13, 49.61, "LU"),
    ("Paris", 2.35, 48.86, "FR"),
    ("Berlin", 13.40, 52.52, "DE"),
    ("Madrid", -3.70, 40.42, "ES"),
    ("Rome", 12.50, 41.90, "IT"),
    ("Stockholm", 18.07, 59.33, "SE"),
    ("Oslo", 10.75, 59.91, "NO"),
    ("Warsaw", 21.01, 52.23, "PL"),
    ("Vienna", 16.37, 48.21, "AT"),
    ("Helsinki", 24.94, 60.17, "FI"),
    ("Copenhagen", 12.57, 55.65, "DK"),
    ("Budapest", 19.04, 47.50, "HU"),
    ("Prague", 14.44, 50.08, "CZ"),
    ("Dublin", -6.26, 53.35, "IE"),
    ("Athens", 23.73, 37.98, "GR"),
    ("Lisbon", -9.14, 38.72, "PT"),
    ("Bratislava", 17.11, 48.15, "SK"),
    ("Ljubljana", 14.51, 46.06, "SI"),
    ("Zagreb", 15.98, 45.81, "HR"),
    ("Bucharest", 26.10, 44.43, "RO"),
    ("Sofia", 23.32, 42.70, "BG"),
    ("Tallinn", 24.75, 59.44, "EE"),
    ("Riga", 24.11, 56.95, "LV"),
    ("Vilnius", 25.28, 54.69, "LT"),
    ("Bern", 7.45, 46.95, "CH"),
    ("London", -0.13, 51.51, "GB"),
    ("Belgrade", 20.46, 44.79, "RS"),
    ("Sarajevo", 18.41, 43.86, "BA"),
    ("Tirana", 19.82, 41.33, "AL"),
    ("Podgorica", 19.26, 42.44, "ME"),
    ("Skopje", 21.43, 41.99, "MK"),
    ("Nicosia", 33.38, 35.10, "CY"),
    ("Valletta", 14.51, 35.90, "MT"),
]


def test_polygon_file_is_present():
    assert is_available(), "resources/geo/countries.geojson missing or empty"


def test_cities_resolve_to_their_own_country():
    """The border cases are the ones the bounding-box version got wrong."""
    wrong = []
    for name, lon, lat, want in CITY_COUNTRY:
        got = country_for(lon, lat)
        if got != want:
            wrong.append(f"{name}: got {got!r}, want {want!r}")
    assert not wrong, "wrong country for: " + "; ".join(wrong)


def test_country_of_uses_the_polygons():
    """The module-level helper must agree with the polygon lookup."""
    assert country_of(6.08, 50.78) == "DE"
    assert country_of(6.90, 52.22) == "NL"
    assert country_of(6.13, 49.61) == "LU"


def test_country_of_returns_empty_outside_europe():
    assert country_of(-100.0, 40.0) == ""
    assert country_of(0.0, 0.0) == ""


def test_iso3_mapping_covers_every_box():
    """Every country in the box list must have an ISO-3 code."""
    missing = [cc for cc in EUROPE_BOXES if cc not in ISO2_TO_ISO3]
    assert not missing, f"no ISO-3 code for: {missing}"


def test_iso3_mapping_targets_are_three_letters():
    for cc, iso3 in ISO2_TO_ISO3.items():
        assert len(iso3) == 3, f"{cc} -> {iso3}"


def test_normalise_keeps_an_explicit_country_tag():
    el = {
        "type": "node",
        "id": 1,
        "lat": 52.37,
        "lon": 4.89,
        "tags": {
            "amenity": "charging_station",
            "hgv": "yes",
            "addr:country": "NL",
        },
    }
    sig = normalise(el)
    assert sig is not None
    assert sig["raw_state"]["country"] == "NL"
    assert sig["raw_state"]["countryIso3"] == "NLD"


def test_normalise_falls_back_to_the_lookup():
    el = {
        "type": "node",
        "id": 2,
        "lat": 48.86,
        "lon": 2.35,
        "tags": {"amenity": "charging_station", "hgv": "yes"},
    }
    sig = normalise(el)
    assert sig is not None
    assert sig["raw_state"]["country"] == "FR"


def test_normalise_accepts_an_iso3_country_tag():
    el = {
        "type": "node",
        "id": 3,
        "lat": 52.52,
        "lon": 13.4,
        "tags": {"amenity": "charging_station", "hgv": "yes", "addr:country": "DEU"},
    }
    sig = normalise(el)
    assert sig is not None
    assert sig["raw_state"]["country"] == "DE"


def test_normalise_without_coordinates_returns_none():
    el = {"type": "node", "id": 4, "tags": {"amenity": "charging_station", "hgv": "yes"}}
    assert normalise(el) is None


def test_truck_gate_rejects_a_private_car_charger():
    """access=private on a 22 kW charger proves nothing about trucks."""
    el = {
        "type": "node",
        "id": 5,
        "lat": 52.37,
        "lon": 4.89,
        "tags": {
            "amenity": "charging_station",
            "access": "private",
            "power": "22 kW",
        },
    }
    sig = normalise(el)
    assert sig is not None
    assert not osm_eu._truck_relevant(sig)


def test_truck_gate_keeps_an_mcs_socket():
    el = {
        "type": "node",
        "id": 6,
        "lat": 52.37,
        "lon": 4.89,
        "tags": {"amenity": "charging_station", "socket:mcs": "yes"},
    }
    sig = normalise(el)
    assert sig is not None
    assert osm_eu._truck_relevant(sig)


def test_truck_gate_keeps_a_high_power_charger():
    el = {
        "type": "node",
        "id": 7,
        "lat": 52.37,
        "lon": 4.89,
        "tags": {
            "amenity": "charging_station",
            "access": "customers",
            "power": "350 kW",
        },
    }
    sig = normalise(el)
    assert sig is not None
    assert osm_eu._truck_relevant(sig)


def test_signal_id_is_namespaced():
    """The Europe-wide ids must not collide with the Dutch osm ingest."""
    el = {
        "type": "node",
        "id": 8,
        "lat": 52.37,
        "lon": 4.89,
        "tags": {"amenity": "charging_station", "hgv": "yes"},
    }
    sig = normalise(el)
    assert sig is not None
    assert sig["signal_id"].startswith("osm_eu:")
    assert sig["source"] == "osm_eu"


def test_priority_order_covers_every_country():
    """No country may be missing from the run order."""
    assert set(osm_eu.PRIORITY_ORDER) == set(EUROPE_BOXES)
