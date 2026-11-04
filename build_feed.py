"""Builds the shared statistics file (the "feed") that the helper downloads when it has no token.

Run by GitHub Actions every 6 hours (.github/workflows/feed.yml) with the STRATZ_TOKEN secret:
    python build_feed.py site/stats.json
The file is the helper's own cache format without the "with allies" table (not used) and without
lane pairs too rare to be used, ~350 KB + lanes.
    python build_feed.py --check-valve site/stats.json
fails when the file has no fresh Valve numbers from our server (the collector or the export stopped).
Next to stats.json it puts the files of the build page, which every helper downloads too:
- builds.json: items, skill order, talents and net worth of strong players (builds.py, STRATZ), rebuilt
  once a day, else the published copy is kept;
- protracker.json: Dota Pro Tracker's builds, fetched weekly by our server (protracker.py); when the server
  has none, the published copy is kept.
"""
import http.client
import json
import os
import sys
import urllib.request
import time
from pathlib import Path

import builds
import data
import protracker
import stratz

MIN_LANE_GAMES = 100  # scoring.MIN_PAIR_GAMES: rarer lane pairs are never used (scoring.py is not in feed-repo)
PROTRACKER_URL = "http://62.109.7.63:8080/protracker.json"  # our server (server/setup.sh); PROTRACKER_URL overrides
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


def _get_json(url):
    """The JSON at `url`, or None when it can't be had."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dota-draft-helper"}),
                                    timeout=120) as resp:
            return json.load(resp)
    except (OSError, ValueError, http.client.HTTPException) as e:
        print(f"{url}: {e}", file=sys.stderr)
        return None


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)", file=sys.stderr)


def build_files(statistics, folder, now=None, get=None):
    """builds.json and protracker.json into `folder`: fresh ones when due and possible, else the published
    copies, so a failure here never takes the build page away from the helpers."""
    now = now or time.time()
    get = get or _get_json
    published = get(data.feed_file_url("builds.json"))
    if published and published.get("format") == builds.FORMAT and now - published["fetched_at"] < builds.REFRESH:
        fresh = published  # rebuilt less than a day ago
    else:
        try:
            fresh = builds.fetch(statistics.positions)
        except Exception as e:  # STRATZ down and so on
            print(f"builds: {e}", file=sys.stderr)
            fresh = published if published and published.get("format") == builds.FORMAT else None
    if fresh:
        _write(folder / "builds.json", fresh)
    pro = get(os.environ.get("PROTRACKER_URL") or PROTRACKER_URL)
    if not (pro and pro.get("format") == protracker.FORMAT and pro.get("heroes")):
        pro = get(data.feed_file_url("protracker.json"))
    if pro and pro.get("format") == protracker.FORMAT and pro.get("heroes"):
        _write(folder / "protracker.json", dict(pro, attribution=protracker.ATTRIBUTION))


def main(target):
    if not stratz.token():
        sys.exit("STRATZ_TOKEN is not set")
    raw = data.collect(lambda n, total: print(f"{n}/{total}", end="\r", file=sys.stderr))
    if raw["source"] != "stratz":
        sys.exit("STRATZ data was not collected")
    build_files(data.HeroData(raw), Path(target).parent)
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
