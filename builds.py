"""Item builds, skill order and talents of every hero on each of its positions, from STRATZ.

Strong players (Divine and Immortal) over the running and the last complete week, like the "7000+ MMR,
last 8 days" of build sites: a patch changes builds fast, and old weeks would mix two patches.
A hero/position with fewer than MIN_MATCHES such games falls back to all ranks.

Per hero and position (STRATZ `heroStats`, every week summed):
- start: items bought before the game, {item: {copies: [games, wins]}};
- boots: {item: [games, wins, average minute]};
- items: first purchase of every other item, {item: [games, wins, median minute, spread of the minute]};
- skills: on which hero level an ability got its last point, {ability: {level: [games, wins]}}
  (the ultimate is not in it: it is always taken on 6, 12 and 18);
- first: on which hero level an ability got its first point, same shape;
- talents: {talent: [games, wins, average minute]};
- networth: the average net worth by minute in every rank, {bracket: [[minute, net worth, games], ...]}
  plus "ALL" (all ranks together); STRATZ's minute 1 is the horn (net worth 600, no last hits), its minute 0
  has nonsense (116) and is dropped (build_advice.target_networth).
Plus the names and costs of items, the names of abilities and every hero's talents by slot (STRATZ
`constants`), and what every item is made of (OpenDota's public constants: STRATZ has no recipes). The
names are the ones GSI sends ("item_desolator", "morphling_waveform"), so the game's data joins them directly.

`python builds.py` downloads everything (~2-3 minutes) to cache/builds.json;
`python builds.py "Phantom Assassin" 1` prints that build.
"""
import json
import sys
import time
from pathlib import Path

import data
import paths
import stratz

BUILDS_FILE = paths.CACHE / "builds.json"
BUNDLED_FILE = paths.BUNDLED / "data" / "builds.json"  # a copy inside the exe: friends have no STRATZ key
FORMAT = 5
BRACKET = "DIVINE_IMMORTAL"
WEEKS = 2  # the running week and the last complete one
MIN_MATCHES = 500  # fewer games at BRACKET: all ranks instead
MIN_POSITION_SHARE = 0.1  # a position with a smaller share of the hero's games is left out
REFRESH = 24 * 3600

KINDS = {
    "start": "itemStartingPurchase", "boots": "itemBootPurchase", "items": "itemFullPurchase",
    "skills": "abilityMaxLevel", "first": "abilityMinLevel", "talents": "talent",
}
FIELDS = {
    "start": "itemId instance matchCount winCount",
    "boots": "itemId matchCount winCount timeAverage",
    "items": "itemId instance time matchCount winCount",
    "skills": "abilityId level matchCount winCount",
    "first": "abilityId level matchCount winCount",
    "talents": "abilityId matchCount winCount timeAverage",
}


def hero_positions(position_games):
    """Positions the hero is really played on: {position: (games, wins)} -> [position, ...]."""
    total = sum(games for games, _ in position_games.values()) or 1
    return sorted(p for p, (games, _) in position_games.items() if games / total >= MIN_POSITION_SHARE)


def _query(hero, positions, weeks, bracket, networth=False):
    """One request for a hero: kind x position x week, as aliased fields "<kind><position>_<week index>";
    with `networth` also the net worth curves of every rank ("networth<position>_<week index>")."""
    extra = f", bracketBasicIds: [{bracket}]" if bracket else ""
    # itemFullPurchase drops (item, minute) rows with few games unless matchLimit is low: a late item is bought
    # at many different minutes, so whole late items were missing (Medusa: no Skadi, no Daedalus; 2026-11-04)
    parts = [f"{kind}{p}_{i}: {field}(heroId: {hero}, week: {w}, positionIds: [POSITION_{p}]{extra}"
             f"{', matchLimit: 1' if kind == 'items' else ''}) {{ {FIELDS[kind]} }}"
             for kind, field in KINDS.items() for p in positions for i, w in enumerate(weeks)]
    if networth:
        parts += [f"networth{p}_{i}: stats(heroIds: [{hero}], week: {w}, positionIds: [POSITION_{p}], groupByTime: true, "
                  f"groupByBracket: true) {{ time bracketBasicIds matchCount networth }}"
                  for p in positions for i, w in enumerate(weeks)]
    return "{ heroStats { " + " ".join(parts) + " } }"


def summarize_networth(rows):
    """Rows of every week and rank -> {bracket: [[minute, average net worth, games]]} and "ALL"."""
    sums = {}  # (bracket, minute) -> [games, net worth * games]
    for row in rows:
        if row.get("networth") is None or not row.get("matchCount") or row["time"] < 1:
            continue
        for bracket in (row["bracketBasicIds"] or "ALL", "ALL") if row["bracketBasicIds"] else ("ALL",):
            cell = sums.setdefault((bracket, row["time"]), [0, 0.0])
            cell[0] += row["matchCount"]
            cell[1] += row["networth"] * row["matchCount"]
    curves = {}
    for (bracket, minute), (games, total) in sorted(sums.items()):
        curves.setdefault(bracket, []).append([minute, round(total / games), games])
    return curves


def summarize(rows_by_kind):
    """Rows of every week of one hero/position -> the summary described at the top."""
    start, boots, items, talents = {}, {}, {}, {}
    skills, first = {}, {}
    minutes = {}  # item -> [(minute, games)]
    for row in rows_by_kind.get("start", []):
        cell = start.setdefault(row["itemId"], {}).setdefault(row["instance"] or 1, [0, 0])
        cell[0] += row["matchCount"]
        cell[1] += row["winCount"]
    for row in rows_by_kind.get("boots", []):
        cell = boots.setdefault(row["itemId"], [0, 0, 0.0])
        cell[2] = (cell[2] * cell[0] + (row["timeAverage"] or 0) / 60 * row["matchCount"]) / max(cell[0] + row["matchCount"], 1)
        cell[0] += row["matchCount"]
        cell[1] += row["winCount"]
    for row in rows_by_kind.get("items", []):
        if (row.get("instance") or 1) != 1:  # the first copy is when the item is "bought"
            continue
        cell = items.setdefault(row["itemId"], [0, 0, 0])
        cell[0] += row["matchCount"]
        cell[1] += row["winCount"]
        minutes.setdefault(row["itemId"], []).append((row["time"], row["matchCount"]))
    for item, spread in minutes.items():
        items[item][2] = _median(spread)
        items[item].append(_spread(spread))
    for kind, table in (("skills", skills), ("first", first)):
        for row in rows_by_kind.get(kind, []):
            cell = table.setdefault(row["abilityId"], {}).setdefault(row["level"], [0, 0])
            cell[0] += row["matchCount"]
            cell[1] += row["winCount"]
    for row in rows_by_kind.get("talents", []):  # STRATZ gives these times in seconds
        cell = talents.setdefault(row["abilityId"], [0, 0, 0.0])
        cell[2] = (cell[2] * cell[0] + (row["timeAverage"] or 0) / 60 * row["matchCount"]) / max(cell[0] + row["matchCount"], 1)
        cell[0] += row["matchCount"]
        cell[1] += row["winCount"]
    matches = max((games for copies in start.values() for games, _ in copies.values()), default=0)
    return {"matches": matches, "start": start, "boots": boots, "items": items, "skills": skills, "first": first,
            "talents": talents}


def _spread(spread):
    """Standard deviation of the minute over (minute, games) pairs, like Dota Pro Tracker's "13m-23m"."""
    games = sum(n for _, n in spread) or 1
    mean = sum(minute * n for minute, n in spread) / games
    return round((sum(n * (minute - mean) ** 2 for minute, n in spread) / games) ** 0.5, 1)


def _median(spread):
    """Median minute of (minute, games) pairs."""
    spread = sorted(spread)
    half, seen = sum(games for _, games in spread) / 2, 0
    for minute, games in spread:
        seen += games
        if seen >= half:
            return minute
    return spread[-1][0] if spread else 0


def _split(answer, positions, weeks):
    """Aliased fields of one answer -> {position: {kind: rows of all weeks}}."""
    result = {p: {kind: [] for kind in KINDS} for p in positions}
    for kind in KINDS:
        for p in positions:
            for i in range(len(weeks)):
                result[p][kind] += answer.get(f"{kind}{p}_{i}") or []
    return result


def fetch_hero(hero, positions, weeks):
    """{position: summary} of one hero: strong players, or all ranks where they have too few games."""
    found = {}
    answer = stratz._query(_query(hero, positions, weeks, BRACKET, networth=True))["heroStats"]
    strong = _split(answer, positions, weeks)
    curves = {p: summarize_networth([row for i in range(len(weeks)) for row in answer.get(f"networth{p}_{i}") or []])
              for p in positions}
    thin = []
    for p in positions:
        summary = summarize(strong[p])
        if summary["matches"] >= MIN_MATCHES:
            found[p] = dict(summary, bracket=BRACKET)
        else:
            thin.append(p)
    if thin:
        everyone = _split(stratz._query(_query(hero, thin, weeks, None))["heroStats"], thin, weeks)
        for p in thin:
            found[p] = dict(summarize(everyone[p]), bracket=None)
    for p in found:
        found[p]["networth"] = curves[p]
    return found


def fetch_constants():
    """{"items": {id: [name, shown name, cost, [component ids], charges a purchase gives (consumables, else 0)]},
    "abilities": {id: [name, shown name, is talent]},
    "talents": {hero: [talent ability id by slot 0-7]}} (slots 0-1 are the level 10 choice, 2-3 level 15...)."""
    answer = stratz._query("{ constants { items { id name displayName stat { cost } } "
                           "abilities { id name isTalent language { displayName } } "
                           "heroes { id talents { abilityId slot } } } }")["constants"]
    recipes = data._get("/constants/items")  # {short name: {"id", "components": [short names]}}
    ids = {short: entry["id"] for short, entry in recipes.items() if "id" in entry}
    parts = {entry["id"]: [ids[c] for c in entry.get("components") or [] if c in ids]
             for entry in recipes.values() if "id" in entry}
    charges = {entry["id"]: entry["charges"] for entry in recipes.values()
               if "id" in entry and entry.get("qual") == "consumable" and isinstance(entry.get("charges"), int)}
    items = {i["id"]: [i["name"], i["displayName"], (i["stat"] or {}).get("cost") or 0, parts.get(i["id"], []),
                       charges.get(i["id"], 0)]
             for i in answer["items"] if i.get("name")}
    abilities = {a["id"]: [a["name"], ((a["language"] or {}).get("displayName") or a["name"]).replace("%%", "%"),
                           bool(a["isTalent"])]  # STRATZ writes some talents as "-40%% Cooldown"
                 for a in answer["abilities"] if a.get("name")}
    talents = {}
    for hero in answer["heroes"]:
        slots = sorted((t["slot"], t["abilityId"]) for t in hero.get("talents") or [])
        talents[hero["id"]] = [ability for _, ability in slots]
    return {"items": items, "abilities": abilities, "talents": talents}


def fetch(position_table, progress=None):
    """Everything for every hero; `position_table` is the statistics' {hero: {position: (games, wins)}}."""
    weeks = stratz.weeks_to_use()[:WEEKS]
    heroes = {}
    for n, (hero, games) in enumerate(sorted(position_table.items()), 1):
        heroes[hero] = fetch_hero(hero, hero_positions(games), weeks)
        if progress:
            progress(n, len(position_table))
        time.sleep(stratz.REQUEST_PAUSE)
    return {"format": FORMAT, "fetched_at": time.time(), "bracket": BRACKET, "weeks": weeks,
            **fetch_constants(), "heroes": heroes}


def save(builds, path=BUILDS_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(builds), encoding="utf-8")


def load(path=None):
    """The saved builds with integer keys again (the own download, else the copy inside the exe), or None.
    A file that can't be read whole (another format, a part missing) is passed over for the next one."""
    def ints(value):
        if isinstance(value, dict):
            return {int(k) if k.lstrip("-").isdigit() else k: ints(v) for k, v in value.items()}
        return value

    for candidate in [path] if path else [BUILDS_FILE, BUNDLED_FILE]:
        try:
            raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
            if raw.get("format") != FORMAT:
                continue
            for key in ("items", "abilities", "talents", "heroes"):
                if not isinstance(raw[key], dict):
                    raise TypeError(key)
                raw[key] = ints(raw[key])
            return raw
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def describe(builds, hero, position):
    """Readable lines of one build, for checking it by eye against other sites."""
    entry = builds["heroes"][hero][position]
    items, abilities = builds["items"], builds["abilities"]
    games = entry["matches"] or 1

    def item(i):
        return items.get(i, [f"#{i}", f"#{i}", 0])[1]

    lines = [f"position {position}, ~{entry['matches']} games, "
             + ("Divine+Immortal" if entry["bracket"] else "all ranks (too few strong games)")]
    start = sorted(((copies[1][0], i, max(copies)) for i, copies in entry["start"].items() if 1 in copies), reverse=True)
    lines.append("start: " + ", ".join(f"{item(i)}" + (f" x{n}" if n > 1 else "") + f" {g / games:.0%}"
                                       for g, i, n in start if g / games >= 0.3))
    boots = sorted(((g, i, w, t) for i, (g, w, t) in entry["boots"].items()), reverse=True)[:3]
    lines.append("boots: " + ", ".join(f"{item(i)} {g / games:.0%} (win {w / g:.1%}, ~{t:.0f} min)" for g, i, w, t in boots))
    core = sorted((row[2], i, row[0], row[1]) for i, row in entry["items"].items()
                  if g / games >= 0.15 and items.get(i, [0, 0, 0])[2] >= 1000)
    lines.append("items by minute: " + ", ".join(f"{item(i)} {t} min ({g / games:.0%}, win {w / g:.1%})" for t, i, g, w in core))
    order = []
    for ability, levels in entry["skills"].items():
        level, (g, _) = max(levels.items(), key=lambda kv: kv[1][0])
        info = abilities.get(ability, [f"#{ability}", f"#{ability}", False])
        if not info[2]:
            order.append((level, info[1], g))
    lines.append("maxed at hero level: " + ", ".join(f"{name} {level}" for level, name, _ in sorted(order))
                 + ", ultimate 6/12/18")
    opening = []
    for ability, levels in entry["first"].items():
        info = abilities.get(ability, [f"#{ability}", f"#{ability}", False])
        if not info[2]:
            level = max(levels.items(), key=lambda kv: kv[1][0])[0]
            opening.append((level, info[1]))
    lines.append("first point at hero level: " + ", ".join(f"{name} {level}" for level, name in sorted(opening)))
    talents = sorted(((g, a) for a, (g, _, _) in entry["talents"].items()), reverse=True)[:4]
    lines.append("talents: " + ", ".join(f"{abilities.get(a, ['', f'#{a}'])[1]} {g / games:.0%}" for g, a in talents))
    return lines


def main():
    if len(sys.argv) == 3:
        builds = load()
        hero_data = data.load_cache()
        print("\n".join(describe(builds, hero_data.find(sys.argv[1]), int(sys.argv[2]))))
        return
    hero_data = data.load_cache()
    builds = fetch(hero_data.positions, lambda n, total: print(f"\r{n}/{total}", end="", flush=True))
    save(builds)
    print(f"\n{len(builds['heroes'])} heroes -> {BUILDS_FILE}")


if __name__ == "__main__":
    main()
