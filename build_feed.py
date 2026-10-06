"""Builds the shared statistics file (the "feed") that the helper downloads when it has no token.

Run by GitHub Actions every 6 hours (.github/workflows/feed.yml) with the STRATZ_TOKEN secret:
    python build_feed.py site/stats.json
The file is the helper's own cache format without the "with allies" table (not used) and without
lane pairs too rare to be used, ~350 KB + lanes.
"""
import json
import sys
from pathlib import Path

import data
import stratz

MIN_LANE_GAMES = 100  # scoring.MIN_PAIR_GAMES: rarer lane pairs are never used (scoring.py is not in feed-repo)


def main(target):
    if not stratz.token():
        sys.exit("STRATZ_TOKEN is not set")
    raw = data.collect(lambda n, total: print(f"{n}/{total}", end="\r", file=sys.stderr))
    if raw["source"] != "stratz":
        sys.exit("STRATZ data was not collected")
    raw.pop("synergy", None)
    raw["lanes"] = {hero: {position: {partner: cell for partner, cell in partners.items() if cell[0] >= MIN_LANE_GAMES}
                           for position, partners in by_position.items()}
                    for hero, by_position in raw.get("lanes", {}).items()}
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    print(f"\nwrote {path} ({path.stat().st_size // 1024} KB), weeks {raw['weeks']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "site/stats.json")
