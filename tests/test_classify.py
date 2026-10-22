"""Unit tests for status classification and the diff state hash."""

from depotmon.classify import status as st


def test_decide_precedence():
    assert st.decide(["permit_requested", "permit_granted"]) == "permit_granted"
    assert st.decide(["permit_granted", "under_construction"]) == "under_construction"
    assert st.decide(["unknown", "rumoured"]) == "rumoured"
    assert st.decide([]) == "unknown"


def test_confidence_bounds_and_ordering():
    one = [{"source": "permits"}]
    three = [{"source": "permits"}, {"source": "osm"}, {"source": "ndw"}]
    c1 = st.confidence_for(one, "permit_requested", has_address=False,
                           has_power=False, multi_source=False)
    c3 = st.confidence_for(three, "operational", has_address=True,
                           has_power=True, multi_source=True)
    assert 0.05 <= c1 <= 0.95
    assert c3 > c1
    assert c3 <= 0.95


def test_rumoured_penalised():
    base = st.confidence_for([{"source": "permits"}], "permit_requested",
                             has_address=True, has_power=True, multi_source=False)
    rum = st.confidence_for([{"source": "permits"}], "rumoured",
                            has_address=True, has_power=True, multi_source=False)
    assert rum < base


def test_state_hash_stable_and_sensitive():
    site = {"status": "operational", "operator": "Fastned", "power_kw_announced": 1000,
            "power_kw_operational": 1000, "n_points": 4, "hgv_capable": True,
            "access": "public", "confidence": 0.8}
    assert st.state_hash(site) == st.state_hash(dict(site))
    changed = dict(site, status="permit_granted")
    assert st.state_hash(changed) != st.state_hash(site)


def test_status_rank_unknown_lowest():
    assert st.status_rank("unknown") < st.status_rank("rumoured") < st.status_rank("operational")
