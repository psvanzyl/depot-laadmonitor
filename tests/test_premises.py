from depotmon.classify import premises


def _item(kind, status, **kw):
    return {
        "kind": kind,
        "operator": kw.pop("operator", "Hartog & Bikker"),
        "city": kw.pop("city", "Zoeterwoude"),
        "postcode": kw.pop("postcode", "2381 AB"),
        "address": kw.pop("address", None),
        "status_hint": status,
        "hgv_capable": kw.pop("hgv_capable", True),
        "power_kw": kw.pop("power_kw", 400),
        "doc_url": kw.pop("doc_url", "https://example.invalid/a"),
        "observed_at": kw.pop("observed_at", "2026-10-15"),
        "raw_state": kw.pop("raw_state", {"lifecycle": status}),
        **kw,
    }


def test_operational_claim_plus_permit_is_tier_a():
    v = premises.score_evidence([
        _item("news", "operational"),
        _item("permit", "permit_granted"),
    ])
    assert v["tier"] == "A"
    assert v["score"] >= 0.85
    assert v["located"] is True


def test_single_plan_without_address_is_only_a_candidate():
    v = premises.score_evidence([
        _item("news", "rumoured", postcode=None,
              raw_state={"lifecycle": "plan"}),
    ])
    assert v["tier"] == "C"
    assert v["located"] is False


def test_public_charge_point_lowers_score():
    base = premises.score_evidence([_item("news", "operational")])
    with_ndw = premises.score_evidence([
        _item("news", "operational"),
        _item("ndw", "operational"),
    ])
    assert with_ndw["score"] < base["score"]
    assert any("counter" in r for r in with_ndw["reasons"])


def test_correlated_items_do_not_stack_linearly():
    one = premises.score_evidence([_item("news", "operational")])
    many = premises.score_evidence([_item("news", "operational") for _ in range(6)])
    assert many["score"] < 0.97
    assert many["score"] - one["score"] <= 0.08


def test_non_hgv_is_capped():
    v = premises.score_evidence([
        _item("news", "operational", hgv_capable=False),
        _item("permit", "permit_granted", hgv_capable=False),
    ])
    assert v["tier"] == "C"
