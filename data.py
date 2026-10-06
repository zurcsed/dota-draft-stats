"""Hero statistics, cached on disk. Where they come from:
- STRATZ with your own token (STRATZ_TOKEN) - the developer's PC and the feed builder;
- the shared feed (FEED_URL): one JSON file built from STRATZ every 6 hours by GitHub Actions
  (build_feed.py), so friends need no token and no setup;
- OpenDota as the last resort (small sample, see CLAUDE.md)."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import paths
import stratz

API = "https://api.opendota.com/api"
CACHE_FILE = paths.CACHE / "opendota.json"
CACHE_TTL = 7 * 24 * 3600  # OpenDota matchup stats move slowly, a week is fine
CACHE_FORMAT = 4  # 4: weeks include the running one; older caches are downloaded again
# (lane partners were added later without a new number, so older programs still read the feed;
# a STRATZ cache without them is simply downloaded again)
RUNNING_WEEK_TTL = 6 * 3600  # the running week keeps filling up
# the shared feed (GitHub Pages); DRAFT_HELPER_FEED overrides it (e.g. a file:// URL for tests)
FEED_URL = "https://zurcsed.github.io/dota-draft-stats/stats.json"  # feed-repo/ on GitHub
FEED_TTL = 2 * 3600  # the feed is rebuilt every 6 hours; checking every 2 is cheap (one ~350 KB file)
REQUEST_PAUSE = 1.1  # free tier allows 60 requests per minute


def _get(path, retries=3):
    req = urllib.request.Request(API + path, headers={"User-Agent": "dota-draft-helper"})
    for attempt in range(retries):
        last = attempt == retries - 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and not last:
                time.sleep(10)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if last:
                raise
            time.sleep(3)


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _int_keys(table):
    return {int(h): {int(o): tuple(v) for o, v in row.items()} for h, row in table.items()}


class HeroData:
    """Heroes with their overall winrate, winrate against and together with every other hero
    and (STRATZ only) games per position and with every lane partner per position."""

    def __init__(self, raw):
        self.fetched_at = raw["fetched_at"]
        self.source = raw.get("source", "opendota")
        self.stratz_error = raw.get("stratz_error")
        self.week = raw.get("week")  # STRATZ: start of the newest week the numbers are from
        self.weeks = raw.get("weeks") or ([self.week] if self.week else [])
        self.bracket = raw.get("bracket")
        self.format = raw.get("format", 1)
        self.via_feed = bool(raw.get("downloaded_at"))
        self.downloaded_at = raw.get("downloaded_at", self.fetched_at)
        self.heroes = {int(i): h for i, h in raw["heroes"].items()}
        self.matchups = _int_keys(raw["matchups"])
        self.synergy = _int_keys(raw.get("synergy", {}))
        self.positions = _int_keys(raw.get("positions", {}))
        self.lanes = {int(h): _int_keys(by_position) for h, by_position in raw.get("lanes", {}).items()}
        # each hero's offset from the table winrate to the STRATZ site's (stratz.py), for the plain list
        self.site_offsets = {int(h): v for h, v in raw.get("site_offsets", {}).items()}
        for h in self.heroes.values():
            h["base_wr"] = h["pub_win"] / h["pub_pick"] if h["pub_pick"] else 0.5

    @property
    def age_days(self):
        return (time.time() - self.fetched_at) / 86400

    @property
    def is_stale(self):
        return time.time() - self.fetched_at > CACHE_TTL

    @property
    def needs_refresh(self):
        """STRATZ: a newer week has ended, the running week data is 6+ hours old, or the rank /
        weeks / cache format changed.
        Feed: downloaded 2+ hours ago, or a token has appeared (then STRATZ is used directly).
        OpenDota: older than a week, or a STRATZ token or a feed has appeared."""
        if self.via_feed:
            return bool(stratz.token()) or time.time() - self.downloaded_at > FEED_TTL
        if self.source == "stratz":
            return (self.format != CACHE_FORMAT or self.bracket != stratz.BRACKET or not self.lanes
                    or not self.site_offsets
                    or self.weeks != stratz.weeks_to_use(self.week) or not self.week
                    or self.week < stratz.last_complete_week()
                    or (stratz.INCLUDE_RUNNING_WEEK and time.time() - self.fetched_at > RUNNING_WEEK_TTL))
        return self.is_stale or bool(stratz.token()) or bool(feed_url())

    def name(self, hero_id):
        hero = self.heroes.get(hero_id)
        return hero["name"] if hero else f"#{hero_id}"

    def find(self, text):
        """Hero id by typed name: exact match first, then prefix, then substring."""
        text = _norm(text)
        if not text:
            return None
        for test in (lambda n: n == text, lambda n: n.startswith(text), lambda n: text in n):
            for hero_id, hero in self.heroes.items():
                if test(_norm(hero["name"])):
                    return hero_id
        return None


def load_cache():
    try:
        return HeroData(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        return None


def _fetch_opendota_matchups(heroes, progress):
    """OpenDota fallback: small sample (~100-150 games per pair), about 2.5 minutes."""
    matchups = {}
    for n, hero_id in enumerate(heroes, 1):
        time.sleep(REQUEST_PAUSE)
        rows = _get(f"/heroes/{hero_id}/matchups")
        matchups[hero_id] = {r["hero_id"]: [r["games_played"], r["wins"]] for r in rows}
        if progress:
            progress(n, len(heroes))
    return matchups


def feed_url():
    return os.environ.get("DRAFT_HELPER_FEED", FEED_URL)


def _save(raw):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.replace(CACHE_FILE)
    return HeroData(raw)


def _download_feed(url):
    request = urllib.request.Request(url, headers={"User-Agent": "dota-draft-helper", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        raw = json.load(resp)
    if raw.get("format") != CACHE_FORMAT:
        raise ValueError("файл статистики другой версии — обнови программу")
    raw["downloaded_at"] = time.time()
    return raw


def fetch(progress=None):
    """Your own STRATZ token first, then the shared feed, then OpenDota. A STRATZ or feed failure
    raises, so the previous data stays in use. Saves the cache and returns it."""
    if not stratz.token() and feed_url():
        return _save(_download_feed(feed_url()))
    return _save(collect(progress))


def collect(progress=None):
    """Download the numbers: hero names from OpenDota; matchups strictly from STRATZ when a token
    is set, else from OpenDota."""
    stats = _get("/heroStats")
    heroes = {
        h["id"]: {
            "name": h["localized_name"],
            "roles": h["roles"],
            "pub_pick": h["pub_pick"],
            "pub_win": h["pub_win"],
        }
        for h in stats
    }
    raw = {"fetched_at": time.time(), "heroes": heroes, "source": "opendota", "format": CACHE_FORMAT}
    if stratz.token():
        # strictly STRATZ: if it fails, the error goes up and the previous STRATZ data stays in use
        numbers = stratz.fetch(list(heroes), progress)
        for hero_id, (games, wins) in numbers["base"].items():
            if hero_id in heroes:
                heroes[hero_id]["pub_pick"], heroes[hero_id]["pub_win"] = games, wins
        raw.update(source="stratz", week=numbers["week"], weeks=numbers["weeks"], bracket=numbers["bracket"],
                   matchups=numbers["matchups"], synergy=numbers["synergy"], positions=numbers["positions"],
                   lanes=numbers["lanes"], site_offsets=numbers["site_offsets"], site_weeks=numbers["site_weeks"])
    if raw["source"] == "opendota":
        raw["matchups"] = _fetch_opendota_matchups(heroes, progress)
    return raw


if __name__ == "__main__":
    # `python data.py` refreshes the cache by hand
    data = fetch(lambda n, total: print(f"\r{n}/{total}", end="", file=sys.stderr))
    print(f"\nSaved {len(data.heroes)} heroes to {CACHE_FILE}")
