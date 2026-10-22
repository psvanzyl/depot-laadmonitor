"""Unit tests for the free-text permit extraction (no network, no database)."""

from depotmon import textutils as tu


def test_power_mw_and_kw_prefers_largest():
    text = "een netaansluiting van 400 kW en een laadplein van 1,2 MW"
    kw, raw = tu.extract_power_kw(text)
    assert kw == 1200.0
    assert "1,2 MW" in raw


def test_power_ignores_implausible_values():
    assert tu.extract_power_kw("5 kW")[0] is None
    assert tu.extract_power_kw("900000 kW")[0] is None


def test_power_kva_treated_as_kw():
    kw, _ = tu.extract_power_kw("transformator van 2500 kVA")
    assert kw == 2500.0


def test_postcode_and_address():
    text = "Op het perceel Uranusstraat 12, 1234AB te Amsterdam wordt een laadplein gerealiseerd."
    pc = tu.extract_postcode(text)
    assert pc == "1234AB"
    addr = tu.extract_address(text, pc)
    assert addr is not None
    assert "Uranusstraat 12" in addr
    assert "1234AB" in addr


def test_applicant_colon_form():
    text = "Aanvrager: De Vries Transport B.V. voor het realiseren van een laadplein."
    assert tu.extract_applicant(text) == "De Vries Transport B.V."


def test_applicant_ontvangen_van():
    text = (
        "Het college heeft op 3 maart 2026 een aanvraag omgevingsvergunning ontvangen van "
        "Van Dijk Logistiek B.V. voor het plaatsen van laadpalen voor vrachtwagens."
    )
    name = tu.extract_applicant(text)
    assert name is not None and "Van Dijk" in name


def test_status_granted_beats_requested():
    text = "De aanvraag omgevingsvergunning is verleend op 12 mei 2026."
    assert tu.status_from_text(text) == "permit_granted"


def test_status_requested():
    text = "Op 1 juni 2026 is een aanvraag omgevingsvergunning ontvangen voor laadpalen."
    assert tu.status_from_text(text) == "permit_requested"


def test_status_policy_doc_is_rumoured():
    text = "Beleidsregel laadinfrastructuur: de gemeente streeft naar snelladers."
    assert tu.status_from_text(text, "Beleidsregel") == "rumoured"


def test_relevance_requires_charge_and_truck_or_power():
    ok, kw = tu.is_relevant("Laadplein met 8 snelladers voor vrachtwagens op bedrijventerrein")
    assert ok and kw["charge"] and kw["truck"]
    ok2, _ = tu.is_relevant("Laadpaal met 1,5 MW voor personenauto's")
    assert ok2  # high power alone is enough
    ok3, _ = tu.is_relevant("Kermis in het dorp met 400 kW geluidsinstallatie")
    assert not ok3


def test_short_tokens_need_word_boundary():
    assert tu.hits("De MCS-standaard wordt toegepast", ["mcs"]) == ["mcs"]
    assert tu.hits("mcsomething else", ["mcs"]) == []


def test_strip_html():
    html = "<p>Laadplein <b>1,2 MW</b></p><script>x=1</script>"
    out = tu.strip_html(html)
    assert "1,2 MW" in out
    assert "x=1" not in out
