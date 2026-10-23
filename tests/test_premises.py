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
    """A public CAR charger still contradicts a private depot claim.

    kind must be the value production actually emits ("chargepoint"). The previous
    version used "ndw", which no ingester produces, so it guarded a dead branch
    while the real counter-evidence never fired.
    """
    # The premises itself must not be HDV-capable, otherwise the public point is
    # part of the same HDV site rather than a contradiction.
    # power_kw=None matters: _item defaults to 400 kW, which would make the news
    # claim itself HDV-capable and exempt the public point from counter-evidence.
    base = premises.score_evidence([
        _item("news", "operational", hgv_capable=False, power_kw=None)])
    with_ndw = premises.score_evidence([
        _item("news", "operational", hgv_capable=False, power_kw=None),
        _item("chargepoint", "operational", hgv_capable=False, power_kw=22),
    ])
    assert with_ndw["score"] < base["score"]
    assert any("counter" in r for r in with_ndw["reasons"])


def test_public_hdv_charge_point_does_not_lower_score():
    """RVO counts public and semi-public HDV points, so the map keeps them
    instead of treating them as counter-evidence."""
    base = premises.score_evidence([_item("news", "operational")])
    with_hdv = premises.score_evidence([
        _item("news", "operational"),
        _item("chargepoint", "operational", hgv_capable=False, power_kw=400),
    ])
    assert with_hdv["score"] >= base["score"]


def test_correlated_items_do_not_stack_linearly():
    one = premises.score_evidence([_item("news", "operational")])
    many = premises.score_evidence([_item("news", "operational") for _ in range(6)])
    assert many["score"] < 0.97
    assert many["score"] - one["score"] <= 0.08


def test_non_hgv_keeps_tier_but_is_flagged():
    """A company-car depot is still a private site: note it, do not bury it."""
    v = premises.score_evidence([
        _item("news", "operational", hgv_capable=False),
        _item("permit", "permit_granted", hgv_capable=False),
    ])
    assert v["tier"] in {"A", "B"}
    assert any("HGV" in r for r in v["reasons"])
