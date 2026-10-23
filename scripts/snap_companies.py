"""Snap depot premises onto their company's own yard."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from depotmon.config import Settings
from depotmon.resolve.snap import snap
from depotmon.store import Store


def main() -> int:
    settings = Settings()
    with Store(settings.database_url) as store:
        print(json.dumps(snap(store), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
