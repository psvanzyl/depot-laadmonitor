"""Fill missing place names from a postcode via PDOK (free, no key, cached in geo_cache)."""
from __future__ import annotations

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store
from .geocode import geocode


def fill_missing_places(store: Store, settings: Settings, limit: int = 120) -> int:
    """Signals that carry a postcode but no place name get one. Returns rows filled."""
    with store.conn.cursor() as cur:
        cur.execute(
            """SELECT signal_id, postcode FROM signals
               WHERE postcode IS NOT NULL AND city IS NULL
                 AND last_seen >= now() - interval '400 days'
               ORDER BY last_seen DESC LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    if not rows:
        return 0

    filled = 0
    with Fetcher(settings, store=store, source="postcodes") as fetcher:
        for row in rows:
            res = geocode(fetcher, store, row["postcode"], postcode=row["postcode"])
            if not res:
                continue
            payload = res.get("payload") or {}
            city = payload.get("woonplaatsnaam") or payload.get("gemeentenaam")
            street = payload.get("straatnaam")
            if not city and not street:
                continue
            with store.tx() as cur:
                cur.execute(
                    """UPDATE signals
                          SET city = COALESCE(city, %s),
                              address = COALESCE(address, %s)
                        WHERE signal_id = %s""",
                    (city, street, row["signal_id"]),
                )
            filled += 1
    return filled
