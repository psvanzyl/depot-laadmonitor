"""Regression tests for change-payload serialisation and operator guards.

Both bugs here had the same shape: the pipeline was green for months because the
guilty code path was never reached. The Decimal crash needed a site that had
actually CHANGED; the Almere artefact needed a municipality that publishes one
document template in bulk. Pin the behaviour so neither returns silently.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from depotmon.diff.runner import _json_default
from depotmon.news_extract import plausible_operator


def test_change_payload_handles_postgres_types():
    """psycopg returns Decimal for numeric and datetime for timestamptz."""
    import json

    payload = {
        "score": Decimal("0.88"),
        "power_kw_announced": Decimal("360.00"),
        "last_seen": datetime(2026, 10, 23, 12, 0),
        "first_seen": date(2026, 10, 1),
    }
    out = json.loads(json.dumps(payload, default=_json_default))
    assert out["score"] == 0.88
    assert out["power_kw_announced"] == 360.0
    assert out["last_seen"] == "2026-10-23T12:00:00"
    assert out["first_seen"] == "2026-10-01"


def test_unknown_types_degrade_to_string_not_crash():
    import json

    assert json.dumps({"x": {1, 2}}, default=_json_default)  # set -> str


@pytest.mark.parametrize(
    "name",
    [
        "Laadplein Heerlenstraat 10 1324MG Almere",  # address used as operator
        "Staatssecretaris Vivianne Heijnen",          # a person with a job title
        "Tankstation",                                # a type of place
        "Distributiecentrum",
    ],
)
def test_not_a_company(name):
    assert plausible_operator(name) is False


@pytest.mark.parametrize(
    "name",
    ["Albert Heijn", "De Klok Logistics", "Hartog & Bikker", "Lidl", "DAF"],
)
def test_real_companies_survive(name):
    assert plausible_operator(name) is True
