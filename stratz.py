"""Hero stats from the STRATZ API: ranked games of all ranks, summed over the last WEEKS complete
weeks - set up to match Dotabuff's counter pages as closely as possible (user's goal 2026-10-05).

Why not "exactly like the site": the stratz.com matchups page has no period argument and always
shows only the running week (from Thursday 00:00 UTC), which is a few dozen games per rare pair and
swings wildly (Broodmother vs Razor: -12 ... +17 from week to week). The API takes any `week`, so
here weeks are summed. The site's "Контрпик" column is reproduced in scoring.py with the same
formula (checked on 8 weeks: winrate - (hero1 winrate - hero2 winrate + 0.5), to 0.1 point).

`stats` and `matchUp` have no game mode filter, but checked 2026-10-04 that they are ranked games:
their game counts stay below the ranked-only totals of `winWeek`, and their winrates match ranked
ones (Phantom Lancer 53.0% vs ranked 53.2%, turbo 56.0%). Weeks start Thursday 00:00 UTC; the
running week is still filling up, so only complete weeks are used. STRATZ keeps 8+ weeks.

Needs a free personal token: stratz.com/api -> log in with Steam -> copy the token, then once:
    setx STRATZ_TOKEN "<token>"
The token is only read from the environment (or the user's environment in the registry,
so a fresh `setx` works without logging out). It is never printed or stored in the project.

Lane partners (`laneOutcome` with isWith: true, one request per position 1/3/4/5): games of a hero on
that position with each hero that stood in the same lane of the same team, and how many of those
matches it won. Without positionIds the API mixes all positions under "POSITION_1" (checked
2026-10-06: Crystal Maiden "POSITION_1" had 286 000 games), so every position is asked separately.

`python stratz.py` checks the token and prints a couple of numbers.
"""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.stratz.com/graphql"
TOKEN_ENV = "STRATZ_TOKEN"
# One RankBracketBasicEnum value: HERALD_GUARDIAN, CRUSADER_ARCHON, LEGEND_ANCIENT, DIVINE_IMMORTAL.
# ("ALL" looks valid but returns nothing - checked 2026-10-04; None = no filter = all ranks.)
# All ranks over the last 4 complete weeks plus the running one is what matches Dotabuff
# ("Противодействие", "В этом месяце") best: checked on 8 heroes / 80 rows (2026-10-05) -
# advantage within 0.43 points, 78 of Dotabuff's top-5 heroes in our top-10. Divine-Immortal only
# was 2.2 points off on winrates; without the running week 74/80; 3 or 5 weeks were worse.
BRACKET = None
WEEKS = 4  # complete weeks, plus the running week (INCLUDE_RUNNING_WEEK)
INCLUDE_RUNNING_WEEK = True
REQUEST_PAUSE = 0.3
POSITIONS = {"POSITION_1": 1, "POSITION_2": 2, "POSITION_3": 3, "POSITION_4": 4, "POSITION_5": 5}
LANE_POSITIONS = (1, 3, 4, 5)  # mid has no lane partner

WEEK = 7 * 86400
KNOWN_WEEK_START = 1788998400  # 2026-09-10 00:00 UTC, a STRATZ week start (Thursday)


def _bracket():
    return f", bracketBasicIds: [{BRACKET}]" if BRACKET else ""


def _positions_query(weeks):
    """All weeks in one request: one aliased field per week (w0 = newest, w1, ...)."""
    parts = [f"w{i}: stats(groupByPosition: true, week: {w}{_bracket()}) {{ heroId position matchCount winCount }}"
             for i, w in enumerate(weeks)]
    return "{ heroStats { " + " ".join(parts) + " } }"


def _matchup_parts(hero, weeks):
    return [f"w{i}: matchUp(heroId: {hero}, week: {w}, take: 200{_bracket()}) {{ heroId "
            f"vs {{ heroId2 matchCount winCount }} with {{ heroId2 matchCount winCount }} }}"
            for i, w in enumerate(weeks)]


def _matchup_query(hero, weeks):
    return "{ heroStats { " + " ".join(_matchup_parts(hero, weeks)) + " } }"


def _hero_query(hero, weeks):
    """Matchups (w<week>) and lane partners (p<position>_<week>) of one hero in one request."""
    lanes = [f"p{p}_{i}: laneOutcome(heroId: {hero}, isWith: true, week: {w}, positionIds: [POSITION_{p}]"
             f"{_bracket()}) {{ heroId2 matchCount matchWinCount }}"
             for p in LANE_POSITIONS for i, w in enumerate(weeks)]
    return "{ heroStats { " + " ".join(_matchup_parts(hero, weeks) + lanes) + " } }"


def last_complete_week(now=None):
    """Start (unix time) of the newest week that has already ended."""
    now = time.time() if now is None else now
    current = KNOWN_WEEK_START + (now - KNOWN_WEEK_START) // WEEK * WEEK
    return int(current - WEEK)


def weeks_to_use(newest=None):
    """Starts of the weeks to sum, newest first: the running week (if INCLUDE_RUNNING_WEEK),
    then WEEKS complete weeks."""
    newest = last_complete_week() if newest is None else newest
    weeks = [newest - k * WEEK for k in range(WEEKS)]
    return [newest + WEEK] + weeks if INCLUDE_RUNNING_WEEK else weeks


def _add(table, key, other, games, wins):
    cell = table.setdefault(key, {}).setdefault(other, [0, 0])
    cell[0] += games or 0
    cell[1] += wins or 0


class StratzError(Exception):
    pass


def token():
    value = os.environ.get(TOKEN_ENV)
    if value:
        return value.strip()
    try:
        import winreg  # Windows only; the feed builder also runs on Linux (GitHub Actions)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, TOKEN_ENV)[0].strip() or None
    except (ImportError, OSError):
        return None


def _query(query, retries=3):
    request = urllib.request.Request(
        API, data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json",
                 "User-Agent": "STRATZ_API"})
    for attempt in range(retries):
        last = attempt == retries - 1
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                answer = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and not last:
                time.sleep(10)
                continue
            if e.code in (401, 403):
                raise StratzError("STRATZ не принял ключ (проверь STRATZ_TOKEN)") from None
            raise StratzError(f"STRATZ ответил HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:  # slow answer or a network hiccup
            if last:
                raise StratzError(f"STRATZ не ответил: {e}") from None
            time.sleep(5)
    if answer.get("errors"):
        raise StratzError("STRATZ: " + "; ".join(e.get("message", "?") for e in answer["errors"]))
    return answer["data"]


def fetch(hero_ids, progress=None):
    """Positions for all heroes, then matchups hero by hero (~2-3 minutes), summed over the weeks.

    Returns {"week": newest complete week start, "weeks": [...], "bracket": BRACKET,
             "base": {hero: [games, wins]}, "positions": {hero: {pos: [games, wins]}},
             "matchups": {hero: {enemy: [games, wins]}}, "synergy": {hero: {ally: [games, wins]}},
             "lanes": {hero: {position: {lane partner: [games, match wins]}}}}
    where wins are always the first hero's wins."""
    if not token():
        raise StratzError(f"нет ключа {TOKEN_ENV}")
    weeks = weeks_to_use()
    answer = _query(_positions_query(weeks))["heroStats"]
    if not any(answer.values()):
        raise StratzError("STRATZ не отдал статистику за последние недели")
    positions, base = {}, {}
    for rows in answer.values():
        for row in rows or []:
            hero = row["heroId"]
            total = base.setdefault(hero, [0, 0])
            total[0] += row["matchCount"] or 0
            total[1] += row["winCount"] or 0
            position = POSITIONS.get(row["position"])
            if position:
                _add(positions, hero, position, row["matchCount"], row["winCount"])
    matchups, synergy, lanes = {}, {}, {}
    for n, hero in enumerate(hero_ids, 1):
        time.sleep(REQUEST_PAUSE)
        for key, entries in _query(_hero_query(hero, weeks))["heroStats"].items():
            if key.startswith("p"):  # p<position>_<week>: lane partners
                for r in entries or []:
                    _add(lanes.setdefault(hero, {}), int(key[1]), r["heroId2"], r["matchCount"], r["matchWinCount"])
                continue
            for entry in entries or []:
                for r in entry["vs"]:
                    _add(matchups, hero, r["heroId2"], r["matchCount"], r["winCount"])
                for r in entry["with"]:
                    _add(synergy, hero, r["heroId2"], r["matchCount"], r["winCount"])
        if progress:
            progress(n, len(hero_ids))
    return {"week": last_complete_week(), "weeks": weeks, "bracket": BRACKET, "base": base, "positions": positions,
            "matchups": matchups, "synergy": synergy, "lanes": lanes}


if __name__ == "__main__":
    if not token():
        raise SystemExit(f"Ключа нет. Создай его на stratz.com/api и выполни: setx {TOKEN_ENV} \"<ключ>\"")
    weeks = weeks_to_use()
    period = f"{time.strftime('%d.%m', time.gmtime(weeks[-1]))}-{time.strftime('%d.%m')}"
    games = wins = 0
    for entries in _query(_matchup_query(61, weeks))["heroStats"].values():
        for r in (entries or [{}])[0].get("vs", []):
            if r["heroId2"] == 15:
                games += r["matchCount"]
                wins += r["winCount"]
    print(f"Ключ работает. {BRACKET or 'все ранги'}, {period}: "
          f"Broodmother против Razor {games} игр, {100 * wins / games:.1f}%")
