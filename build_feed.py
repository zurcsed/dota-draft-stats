"""Builds the shared statistics file (the "feed") that the helper downloads when it has no token.

Run by GitHub Actions every 6 hours (.github/workflows/feed.yml) with the STRATZ_TOKEN secret:
    python build_feed.py site/stats.json
The file is the helper's own cache format without the "with allies" table (not used) and without
lane pairs too rare to be used, ~350 KB + lanes.
    python build_feed.py --check-valve site/stats.json
fails when the file has no fresh Valve numbers from our server (the collector or the export stopped).
"""
import json
import sys
import time
from pathlib import Path

import data
import stratz

MIN_LANE_GAMES = 100  # scoring.MIN_PAIR_GAMES: rarer lane pairs are never used (scoring.py is not in feed-repo)
# the server exports once a day, so its newest match is up to ~a day old; older means it stopped
VALVE_FRESH_HOURS = 36


def valve_problem(raw, now=None):
    """Why the feed's Valve numbers are missing or old, or None when they are fine."""
    valve = raw.get("valve")
    if not valve:
        return "no Valve numbers in the feed: the server's file is missing, too small or older than 3 days"
    hours = ((now or time.time()) - valve["to"]) / 3600
    if hours > VALVE_FRESH_HOURS:
        return f"the newest Valve match is {hours:.0f} hours old: is the export on the server running?"
    return None


def main(target):
    if not stratz.token():
        sys.exit("STRATZ_TOKEN is not set")
    raw = data.collect(lambda n, total: print(f"{n}/{total}", end="\r", file=sys.stderr))
    if raw["source"] != "stratz":
        sys.exit("STRATZ data was not collected")
    # collect() already put our server's Valve numbers in (VALVE_STATS_URL or data.VALVE_URL); say if it did not
    print("Valve numbers: " + (f"{raw['valve']['matches']} matches" if raw.get("valve") else "none, STRATZ's stay"),
          file=sys.stderr)
    raw.pop("synergy", None)
    raw["lanes"] = {hero: {position: {partner: cell for partner, cell in partners.items() if cell[0] >= MIN_LANE_GAMES}
                           for position, partners in by_position.items()}
                    for hero, by_position in raw.get("lanes", {}).items()}
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    print(f"\nwrote {path} ({path.stat().st_size // 1024} KB), weeks {raw['weeks']}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check-valve"]:
        problem = valve_problem(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        sys.exit(problem)  # None = success
    main(sys.argv[1] if len(sys.argv) > 1 else "site/stats.json")
