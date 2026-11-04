"""Builds of strong players from Dota Pro Tracker (dota2protracker.com).

Used by agreement with the site's support (2026-11-03): /api/heroes/list and /api/hero/<id>/builds for
"dota-draft-helper", free, no key. Their terms: not more than one request per 2 seconds, never parallel,
no cache-busting parameters, the 20:00-08:00 UTC window preferred, after 403/429 a pause (Retry-After when
the answer has it); the window and the app's description say "данные: Dota Pro Tracker" with a link to
dota2protracker.com; no proxies for third parties; a paid subscription of ours has to be told to them.
Fetched only by our server (server/protracker-fetch.timer: every night at 20:30 UTC it checks, once a week
it downloads, and the next nights it asks again for what the site refused); GitHub Actions puts the file
into the shared feed (build_feed.py) and every helper downloads it from there, never from the site.

Per hero and position (the site's 7000+ MMR games of the last 14 days, one patch):
- skills: the most taken orders of the first 10 skill points, [{"order": [ability ids], "games", "wins",
  "win_rate", "share"}] (Valve ability ids, the same as STRATZ's and the game's);
- talents: [{"level", "options": [{"ability", "name", "games", "win_rate", "share"}, ...two]}];
- start: the most taken starting inventories, [{"items": [item ids], "games", "win_rate"}];
- items: the site's item table: [{"item", "core", "share", "minute", "spread"}], the core build ("основной")
  by minute, then the rest by share; share is the site's "Частота покупки" (of the games that lasted until
  the item's time), minute and spread the average time and its spread ("18m (13m-23m)").

`python protracker.py` downloads everything (~15-30 minutes, longer when the site refuses) to
cache/protracker.json, saving as it goes: a build the site keeps refusing stays as it was and is asked
again next time;
`python protracker.py --to public/protracker.json --weekly` (the server): only when that file is a week old
or has refused builds, and up to PASSES times while the site still refuses some and the night lasts;
`python protracker.py Juggernaut 1` prints that build.
"""
import email.utils
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import paths

API = "https://dota2protracker.com/api"
FILE = paths.CACHE / "protracker.json"
BUNDLED_FILE = paths.BUNDLED / "data" / "protracker.json"  # a copy inside the exe for friends
FORMAT = 1
SOURCE = "Dota Pro Tracker"
SOURCE_URL = "https://dota2protracker.com"
PAUSE = 3.0  # seconds between requests; the agreed limit is one per 2 seconds
REFUSED_PAUSE = 60  # after a 403/429 without Retry-After (one in five on 2026-11-03), then longer each time
RETRIES = 3
WINDOW_UTC = (20, 8)  # hours: the site prefers the night; automatic updates keep to it
SAVE_EVERY = 10
MIN_MATCHES = 100  # fewer games of a hero on a position on the site: no build for it
MIN_SHARE = 0.1  # a position with a smaller share of the hero's games is left out
TOP_ORDERS = 3
TOP_STARTS = 5
ITEM_VERSION = 2  # what a saved build holds; an older one is downloaded again (2: "core" and "spread" of items)
REFRESH = 7 * 24 * 3600  # their numbers cover 14 days: once a week is plenty
WEEKLY = REFRESH - 12 * 3600  # --weekly: the nightly timer starts the new download on the 7th night, not the 8th
KEEP_FRESH = 3 * 24 * 3600  # a build got less than this ago is not asked again (a refused one's retry, a stopped run)
PASSES = 3  # --weekly: more passes for the refused builds while the night lasts


def _get(path, retries=RETRIES, sleep=time.sleep):
    request = urllib.request.Request(API + path, headers={"User-Agent": "dota-draft-helper", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429, 500, 502, 503) or attempt == retries - 1:
                raise
            sleep(_retry_after(e.headers.get("Retry-After")) or REFUSED_PAUSE * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            sleep(5)


def _retry_after(value):
    """Seconds a Retry-After header asks for (a number or an HTTP date), None without one."""
    if not value:
        return None
    if value.strip().isdigit():
        return int(value)
    try:
        return max(email.utils.parsedate_to_datetime(value).timestamp() - time.time(), 0)
    except (TypeError, ValueError):
        return None


def in_window(now=None):
    """True in the hours the site prefers for automatic downloads (20:00-08:00 UTC)."""
    hour = time.gmtime(now if now is not None else time.time()).tm_hour
    return hour >= WINDOW_UTC[0] or hour < WINDOW_UTC[1]


def hero_positions(row):
    """Positions 1-5 a hero of the site's hero list is really played on."""
    games = {p: row.get(f"pos {p} matches") or 0 for p in range(1, 6)}
    total = sum(games.values()) or 1
    return [p for p, n in games.items() if n >= MIN_MATCHES and n / total >= MIN_SHARE]


def summarize(build):
    """The parts of one /hero/<id>/builds answer the helper shows."""
    data = build["build_data"]
    skills = [{"order": order, "games": stats["count"], "wins": stats["wins"], "win_rate": stats["win_rate"],
               "share": stats["pr"]} for order, stats in data.get("abilities_new") or []][:TOP_ORDERS]
    talents = []
    for tier in data.get("talents") or []:
        options = [{"ability": side["ability_id"], "name": side.get("displayName") or side.get("name"),
                    "games": side.get("count", 0), "win_rate": side.get("win_rate"), "share": side.get("pick_rate")}
                   for side in (tier.get("left"), tier.get("right")) if side]
        talents.append({"level": tier.get("lvl"), "options": options})
    start = [{"items": items, "games": stats["count"], "win_rate": stats["win_rate"]}
             for items, stats in data.get("starting_items_new") or []][:TOP_STARTS]
    def row_of(row, core):
        return {"item": row["raw_item_id"], "core": core, "share": row.get("rel_pr", row["pr"]),
                "minute": row["avg_minute"], "spread": row.get("std_minute") or 0}

    core = sorted((row_of(row, True) for row in data.get("anchor_items") or []), key=lambda row: row["minute"])
    seen = {row["item"] for row in core}
    rest = []
    for row in data.get("items_mid_late") or []:
        if row["raw_item_id"] not in seen:  # a core item, or a second Divine Rapier: shown once
            seen.add(row["raw_item_id"])
            rest.append(row_of(row, False))
    items = core + sorted(rest, key=lambda row: -row["share"])
    scope = build.get("data_scope") or data.get("data_scope") or {}
    return {"games": build["num_matches"], "win_rate": data.get("win_rate"), "skills": skills, "talents": talents,
            "start": start, "items": items, "days": scope.get("configured_window_days"),
            "patch": (scope.get("patch_versions") or [None])[-1]}


def fetch(progress=None, pause=PAUSE, path=FILE, sleep=time.sleep):
    """Every hero on each of its positions, one request after another, saved every SAVE_EVERY builds:
    {"format", "fetched_at", "source", "source_url", "heroes": {hero: {position: build}}, "refused": [[hero, position]]}.
    A build the site keeps refusing keeps its old copy (and is in "refused")."""
    old = load(path) or {}  # a refused build keeps its last copy
    result = {"format": FORMAT, "fetched_at": time.time(), "source": SOURCE, "source_url": SOURCE_URL,
              "heroes": old.get("heroes", {}), "refused": []}
    rows = _get("/heroes/list", sleep=sleep)
    jobs = [(row["hero_id"], p) for row in rows for p in hero_positions(row)]
    for n, (hero, position) in enumerate(jobs, 1):
        kept = result["heroes"].get(hero, {}).get(position)
        if kept and kept.get("v") == ITEM_VERSION and result["fetched_at"] - kept.get("fetched_at", 0) < KEEP_FRESH:
            continue  # got lately (a run that stopped half way goes on where it was)
        sleep(pause)
        try:
            answer = _get(f"/hero/{hero}/builds?position=pos%20{position}", sleep=sleep)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429):
                raise
            result["refused"].append([hero, position])
            answer = None
        main = max(answer or [], key=lambda build: build.get("num_matches") or 0, default=None)  # facets: the most played
        if main and main.get("build_data"):
            result["heroes"].setdefault(hero, {})[position] = dict(summarize(main), fetched_at=time.time(), v=ITEM_VERSION)
        if n % SAVE_EVERY == 0:
            save(result, path)
        if progress:
            progress(n, len(jobs))
    save(result, path)
    return result


def save(builds, path=FILE):
    """Written whole at once: the server hands the file out while the next one is being made."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(builds), encoding="utf-8")
    tmp.replace(path)


def due(saved, now=None):
    """--weekly: download now? When there is no file, it is about a week old, or the site refused builds."""
    if not saved:
        return True
    return (now if now is not None else time.time()) - saved["fetched_at"] >= WEEKLY or bool(saved.get("refused"))


def load(path=None):
    """The saved builds with number keys again (the own download, else the copy inside the exe), or None."""
    raw = None
    for candidate in [path] if path else [FILE, BUNDLED_FILE]:
        try:
            raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    if raw is None:
        return None
    if raw.get("format") != FORMAT:
        return None
    raw["heroes"] = {int(h): {int(p): b for p, b in positions.items()} for h, positions in raw["heroes"].items()}
    return raw


def describe(build, abilities, items):
    """Readable lines of one build; `abilities` / `items` are the names from builds.py (STRATZ constants)."""
    def ability(a):
        return abilities.get(a, ["", f"#{a}"])[1]

    def item(i):
        return items.get(i, ["", f"#{i}"])[1]

    lines = [f"{build['games']} games, win {build['win_rate']:.1%}, last {build['days']} days, patch {build['patch']}"]
    for n, skill in enumerate(build["skills"], 1):
        lines.append(f"skills {n}: " + " > ".join(ability(a) for a in skill["order"])
                     + f"  ({skill['games']} games, {skill['share']:.0%}, win {skill['win_rate']:.1%})")
    for tier in build["talents"]:
        lines.append(f"talent {tier['level']}: " + " | ".join(
            f"{o['name']} {o['share']:.0%} win {o['win_rate']:.1%}" for o in tier["options"] if o["win_rate"] is not None))
    for start in build["start"][:1]:
        lines.append("start: " + ", ".join(item(i) for i in start["items"]) + f" ({start['games']} games, win {start['win_rate']:.1%})")
    lines.append("items: " + ", ".join(f"{item(row['item'])} {row['minute']:.0f} min {row['share']:.0%}"
                                       for row in build["items"] if row["share"] >= 0.15))
    return lines


def main():
    if len(sys.argv) == 3 and not sys.argv[1].startswith("--"):
        import builds
        import data
        names = builds.load()
        hero = data.load_cache().find(sys.argv[1])
        print(f"source: {SOURCE}")
        print("\n".join(describe(load()["heroes"][hero][int(sys.argv[2])], names["abilities"], names["items"])))
        return
    args = sys.argv[1:]
    path = Path(args[args.index("--to") + 1]) if "--to" in args else FILE
    weekly = "--weekly" in args
    if weekly and not due(load(path)):
        print(f"{path} is fresh, nothing refused: nothing to do")
        return
    for _ in range(PASSES if weekly else 1):
        result = fetch(None if weekly else lambda n, total: print(f"\r{n}/{total}", end="", flush=True), path=path)
        print(f"\n{sum(len(p) for p in result['heroes'].values())} hero positions -> {path}, "
              f"refused {len(result['refused'])}: {result['refused']}", flush=True)
        if not result["refused"] or not in_window():
            break


if __name__ == "__main__":
    main()
