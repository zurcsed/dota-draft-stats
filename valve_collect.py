"""Collects public Dota 2 matches straight from Valve (Steam Web API) for our own statistics.

Every public match gets a sequence number in the order it ended; GetMatchHistoryBySequenceNum returns
up to 100 matches from a number on, so walking it misses nothing (OpenDota, STRATZ and Dotabuff start
from the same feed). Kept per match: the 10 heroes and their sides, the winner, game mode, lobby type,
duration and start time - in cache/valve_matches.sqlite. Valve gives no player ranks and no positions or
lanes (sites get those by parsing replays), so those stay with STRATZ.

Needs a free Steam Web API key (steamcommunity.com/dev/apikey; Steam gives it only to an account that has
spent $5 in the store), once: setx STEAM_API_KEY "<key>". It is read from the environment (or the user's
environment in the registry, so a fresh `setx` works at once) and never printed or stored.

    python valve_collect.py              collects until stopped (Ctrl+C), continuing where it stopped
    python valve_collect.py --hours 24   ...for a day
"""
import http.client
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import paths

API = "https://api.steampowered.com/IDOTA2Match_570/"
KEY_ENV = "STEAM_API_KEY"
DB_FILE = paths.CACHE / "valve_matches.sqlite"
LOG_FILE = paths.LOGS / "valve_collect.log"
PAUSE = 1.0  # seconds between requests; a key may make 100 000 a day
BATCH = 100  # the most GetMatchHistoryBySequenceNum returns at once
WAIT_AT_HEAD = 15  # no new matches yet: we have caught up with the games just finished
RETRY_PAUSES = (5, 15, 30, 60, 120)  # Valve's Dota API answers 429 / 5xx now and then
RETRY_CODES = (429, 500, 502, 503, 504)
OUTAGE_WAIT = 300  # still no answer after the retries: the API is down (it happens for hours), wait it out
# a dropped connection or a cut-off body while reading the answer is raised outside URLError
NETWORK_ERRORS = (urllib.error.URLError, OSError, http.client.HTTPException, ValueError)


def key():
    value = os.environ.get(KEY_ENV)
    if value:
        return value.strip()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
            return winreg.QueryValueEx(reg, KEY_ENV)[0].strip() or None
    except (ImportError, OSError):
        return None


def _get(method, **params):
    url = API + method + "/v1/?" + urllib.parse.urlencode({"key": key(), **params})
    request = urllib.request.Request(url, headers={"User-Agent": "dota-draft-helper"})
    for pause in RETRY_PAUSES + (None,):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return json.load(resp)["result"]
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise SystemExit("Valve did not accept the key (403): check STEAM_API_KEY") from None
            if pause is None or e.code not in RETRY_CODES:
                raise
        except NETWORK_ERRORS:
            if pause is None:
                raise
        time.sleep(pause)


def _outage(error):
    """Valve's trouble (server errors, rate limit, no connection) rather than a mistake of ours."""
    return not isinstance(error, urllib.error.HTTPError) or error.code in RETRY_CODES


def open_db(path=DB_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")  # a long read (valve_stats.py) must not block the collector's writes
    db.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id INTEGER PRIMARY KEY, seq INTEGER, start_time INTEGER, duration INTEGER,
        lobby INTEGER, mode INTEGER, radiant_win INTEGER, radiant TEXT, dire TEXT)""")
    db.execute("CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value INTEGER)")
    return db


def row(match):
    """The stored row of a match, or None for one that is no use for hero statistics."""
    players = match.get("players") or []
    heroes = [p.get("hero_id", 0) for p in players]
    if match.get("human_players") != 10 or len(players) != 10 or 0 in heroes:
        return None
    radiant = [p["hero_id"] for p in players if p.get("player_slot", 0) < 128]  # 0-4 Radiant, 128-132 Dire
    dire = [p["hero_id"] for p in players if p.get("player_slot", 0) >= 128]
    if len(radiant) != 5 or len(dire) != 5:
        return None
    return (match["match_id"], match["match_seq_num"], match.get("start_time"), match.get("duration"),
            match.get("lobby_type"), match.get("game_mode"), int(bool(match.get("radiant_win"))),
            ",".join(map(str, radiant)), ",".join(map(str, dire)))


def _state(db, name, value=None):
    if value is not None:
        db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (name, value))
        return value
    found = db.execute("SELECT value FROM state WHERE name = ?", (name,)).fetchone()
    return found[0] if found else None


def collect(hours=None, db_path=DB_FILE, log=print):
    """Walk the match feed from where we stopped (or from the newest match) and store every match."""
    if not key():
        raise SystemExit(f"No Steam Web API key: get one at steamcommunity.com/dev/apikey, then "
                         f'setx {KEY_ENV} "<key>"')
    db = open_db(db_path)
    try:
        return _walk(db, hours, log)
    finally:  # also on Ctrl+C: everything stored so far is committed
        db.close()


def _walk(db, hours, log):
    seq = _state(db, "next_seq")
    if seq is None:  # first run: start at the newest match, collect what ends from now on
        seq = _get("GetMatchHistory", matches_requested=1)["matches"][0]["match_seq_num"]
    stop = time.time() + hours * 3600 if hours else None
    started, seen, kept, outage = time.time(), 0, 0, False
    while stop is None or time.time() < stop:
        try:
            matches = _get("GetMatchHistoryBySequenceNum", start_at_match_seq_num=seq,
                           matches_requested=BATCH).get("matches") or []
        except NETWORK_ERRORS as e:
            if not _outage(e):
                raise
            log(f"{time.strftime('%H:%M')}  Valve API is not answering ({e}), trying again in "
                f"{OUTAGE_WAIT // 60} min")
            outage = True
            time.sleep(OUTAGE_WAIT)
            log(f"{time.strftime('%H:%M')}  trying again now")
            continue
        if outage:
            outage = False
            log(f"{time.strftime('%H:%M')}  Valve API is answering again")
        if not matches:
            time.sleep(WAIT_AT_HEAD)
            continue
        rows = [r for r in map(row, matches) if r]
        db.executemany("INSERT OR IGNORE INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        seq = _state(db, "next_seq", matches[-1]["match_seq_num"] + 1)
        db.commit()
        seen, kept = seen + len(matches), kept + len(rows)
        if seen // 1000 != (seen - len(matches)) // 1000:
            hours_run = (time.time() - started) / 3600
            log(f"{time.strftime('%H:%M')}  matches seen {seen}, kept {kept} ({kept / hours_run:.0f} per hour), "
                f"latest ended {time.strftime('%d.%m %H:%M', time.localtime(matches[-1].get('start_time', 0) + matches[-1].get('duration', 0)))}")
        time.sleep(PAUSE)
    return seen, kept


def disable_quick_edit():
    """A click in the console window starts text selection, which freezes the output and the program with it."""
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.restype = ctypes.c_void_p
        kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        handle, mode = kernel.GetStdHandle(-10), ctypes.c_uint32()  # -10: standard input
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, (mode.value & ~0x40) | 0x80)  # quick edit off, extended flags on
    except (AttributeError, OSError):  # not Windows, or not a console
        pass


def single_instance():
    """The lock file's handle (keep it open), or None when another collector already runs."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(DB_FILE.with_suffix(".lock"), "a")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def log_line(message):
    """Print, and keep the line in logs/valve_collect.log: the window may be closed or frozen."""
    print(message)
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%d.%m %H:%M:%S')}  {message}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    run_hours = float(args[args.index("--hours") + 1]) if "--hours" in args else None
    disable_quick_edit()
    lock = single_instance()
    if lock is None:
        raise SystemExit("The collector is already running in another window")
    log_line(f"started ({run_hours or 'no limit'} h)")
    try:
        total, stored = collect(run_hours, log=log_line)
        log_line(f"done: {total} matches seen, {stored} kept in {DB_FILE}")
    except KeyboardInterrupt:
        log_line("stopped; the next run continues from here")
    except BaseException as e:
        log_line(f"crashed: {e!r}")
        raise
