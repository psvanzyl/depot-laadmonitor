"""Delete evidence rows that are headline fragments, not companies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from depotmon.config import Settings
from depotmon.news_extract import plausible_city, plausible_operator
from depotmon.store import Store

import re

# Vocabulary that puts an article in the depot business rather than the showroom.
LOGISTICS = re.compile(
    r"transport|logistiek|vrachtwagen|truck|bestelbus|bestelwagen|distributie|depot|"
    r"laadplein|laadplekken|laadinfrastructuur|laadstation|snellader|megawatt|"
    r"opslag|warehouse|koerier|vervoerder|expeditie|wagenpark|heavy.?duty|zero.?emission",
    re.I,
)
# A verkeersbesluit reserving street parking bays for EV charging is a PUBLIC
# charge point, not a private depot. Almere alone files ~115 of these from one
# template, which is why the map looked like Almere was the depot capital.
PUBLIC_BAY = re.compile(r"parkeervak|parkeerplaats|parkeerstrook", re.I)
EV_CHARGING = re.compile(r"laden van elektrische voertuigen|laadpunt|oplaadpunt", re.I)

COMPANY_FORM = re.compile(r"\b(B\.?V\.?|N\.?V\.?|Logistics|Logistiek|Transport|Vervoer|Groep|Group)\b")


def main() -> int:
    settings = Settings()
    with Store(settings.database_url) as store:
        with store.tx() as cur:
            cur.execute("SELECT signal_id, kind, operator, city, text_excerpt FROM signals")
            rows = cur.fetchall()
            bad = []
            for r in rows:
                if r["operator"] and not plausible_operator(r["operator"]):
                    bad.append(r["signal_id"])
                    continue
                if r["city"] and not plausible_city(r["city"]):
                    bad.append(r["signal_id"])
                    continue
                text_all = (r["text_excerpt"] or "") + " " + (r["operator"] or "")
                if (
                    r["kind"] == "permit"
                    and PUBLIC_BAY.search(text_all)
                    and EV_CHARGING.search(text_all)
                ):
                    bad.append(r["signal_id"])
                    continue
                if r["kind"] == "news":
                    text = (r["operator"] or "") + " " + (r["text_excerpt"] or "")
                    if not LOGISTICS.search(text) and not COMPANY_FORM.search(r["operator"] or ""):
                        bad.append(r["signal_id"])
            if bad:
                cur.execute("DELETE FROM signals WHERE signal_id = ANY(%s)", (bad,))
            cur.execute("SELECT kind, count(*) FROM signals GROUP BY kind ORDER BY 2 DESC")
            left = cur.fetchall()
    print({"deleted": len(bad), "remaining": {r["kind"]: r["count"] for r in left}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
