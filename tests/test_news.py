from datetime import date

from depotmon import news_extract as nx
from depotmon.ingest import news


HARTOG = (
    "Hartog & Bikker neemt twee e-trucks én een eigen laadplein in gebruik "
    "Hartog & Bikker heeft twee elektrische Mercedes-Benz eActros 600-trekkers en een "
    "eigen laadplein in Zoeterwoude in gebruik genomen. Voor de inzet van de "
    "elektrische trekkers heeft Hartog & Bikker op de vestiging een laadplein "
    "aangelegd met meerdere laders van 400 kW. Binnenkort komt daar een "
    "megawattlader bij. Het laadplein telt 12 laadplekken."
)

AH_DC = (
    "Albert Heijn wint Logistiek Laadplein Award 2026 "
    "Het laadplein van Albert Heijn bij het distributiecentrum in Zaandam heeft de "
    "award gewonnen. Het laadplein telt zeventien laadpunten met een gezamenlijk "
    "vermogen van 3.600 kW."
)


def test_extract_operator_from_headline():
    assert nx.extract_operator("Hartog & Bikker neemt twee e-trucks in gebruik") == "Hartog & Bikker"
    assert nx.extract_operator("Oegema zoekt laadplek voor twee e-trucks") == "Oegema"


def test_extract_city_skips_noise():
    assert nx.extract_city("Oegema zoekt laadplek in Apeldoorn", "") == "Apeldoorn"


def test_points_and_thousands_separator():
    assert nx.extract_points(HARTOG) == 12
    assert nx.power_kw("een gezamenlijk vermogen van 3.600 kW") == 3600.0
    assert nx.power_kw("meerdere laders van 400 kW") == 400.0


def test_normalise_article_keeps_private_site():
    sig = news.normalise_article(
        "https://www.ttm.nl/x/179889/", "Hartog & Bikker neemt twee e-trucks én een "
        "eigen laadplein in gebruik", HARTOG, "https://www.ttm.nl/feed/", date(2026, 10, 15)
    )
    assert sig is not None
    assert sig["operator"] == "Hartog & Bikker"
    assert sig["city"] == "Zoeterwoude"
    assert sig["status_hint"] == "operational"
    assert sig["hgv_capable"] is True
    assert sig["access"] == "private"
    assert sig["power_kw"] == 400.0  # largest explicit figure; MW floor is a fallback
    assert sig["raw_state"]["lifecycle"] == "opening"
    assert sig["raw_state"]["geo_precision"] == "city"


def test_normalise_article_marks_award_case():
    sig = news.normalise_article("https://www.ttm.nl/y", "Albert Heijn wint Logistiek "
                                 "Laadplein Award 2026", AH_DC,
                                 "https://www.ttm.nl/feed/", date(2026, 10, 8))
    assert sig is not None
    assert sig["operator"].startswith("Albert Heijn")
    assert sig["raw_state"]["lifecycle"] == "award"
    assert sig["power_kw"] == 3600.0


def test_normalise_article_rejects_consumer_story():
    sig = news.normalise_article("https://x", "Nieuwe laadpaal in de woonwijk",
                                 "De gemeente plaatst een laadpaal voor bewoners.",
                                 "f", None)
    assert sig is None
