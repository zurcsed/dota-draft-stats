"""Keep independent, validated copies of the published feed on our server.

No STRATZ or Pro Tracker requests: this job only reads our GitHub Pages.
The server's native protracker.json remains owned by protracker-fetch.
Run: python feed_mirror.py --to /opt/dota-collector/public
"""
import argparse
import json
import time
from pathlib import Path

import builds
import data
import freshness
import protracker


def read_stats(path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format") != data.CACHE_FORMAT or not raw.get("heroes") or not raw.get("matchups"):
            return None
        return data.HeroData(raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def mirror(folder, now=None):
    now = time.time() if now is None else now
    folder.mkdir(parents=True, exist_ok=True)
    errors = {}
    for name, version, loader in (("stats", data.CACHE_FORMAT, read_stats),
                                   ("builds", builds.FORMAT, builds.load)):
        path = folder / (name + ".json")
        have = loader(path)
        stamp = have.fetched_at if name == "stats" and have else (have or {}).get("fetched_at", 0)
        try:
            data.download_newer(data.FEED_URL.rsplit("/", 1)[0] + "/" + name + ".json",
                                path, version, stamp, loader, strict=True)
        except Exception as e:
            errors[name] = type(e).__name__
    stats, items, pro = read_stats(folder / "stats.json"), builds.load(folder / "builds.json"), protracker.load(folder / "protracker.json")
    checks = []
    for name, stamp, age in (("stats", stats.fetched_at if stats else None, 36 * 3600),
                              ("builds", (items or {}).get("fetched_at"), 48 * 3600),
                              ("protracker", (pro or {}).get("fetched_at"), 8 * 86400)):
        text, warning = freshness.describe(name, stamp, age, errors.get(name), now)
        checks.append({"name": name, "fetched_at": stamp, "warning": warning, "message": text})
    health = {"checked_at": now, "files": checks, "protracker": freshness.pro_summary(pro, now)}
    target = folder / "health.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(health), encoding="utf-8")
    tmp.replace(target)
    print(json.dumps(health), flush=True)
    return not any(row["warning"] for row in checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", type=Path, required=True)
    raise SystemExit(0 if mirror(parser.parse_args().to) else 1)
