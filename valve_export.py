"""Our own hero statistics from the matches valve_collect.py stored, in the helper's cache format.

    python valve_export.py                          writes cache/valve_stats.json (last 7 days of All Pick)
    python valve_export.py out.json 14              another file and period
    python valve_export.py out.json 7 --prune 14    ...and deletes the stored matches older than 14 days
    python valve_export.py --seed seed.sqlite 14    a copy of the last 14 days for the server

data.py puts these numbers (winrates and counters) in place of STRATZ's; positions and lane partners
stay with STRATZ, Valve does not give them. All Pick of every lobby type matched Dotabuff best.
The matches are read one by one, so a small server (512 MB) is enough.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import paths
import valve_collect
import valve_stats

ALL_PICK = 22
DAYS = 7
FILE = paths.CACHE / "valve_stats.json"


def build(days=DAYS, db_path=valve_collect.DB_FILE):
    """{"matchups": {hero: {enemy: [games, hero's wins]}}, "base": {hero: [games, wins]}, "matches": n,
    "from"/"to": start times of the first and last match, "days": days, "built_at": now}.
    The period ends at the newest stored match, not at the clock: the collector may lag behind."""
    db = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)
    try:
        newest = db.execute("SELECT MAX(start_time) FROM matches").fetchone()[0]
        if newest is None:
            raise ValueError("no matches stored yet")
        start = newest - days * 86400
        rows = db.execute("SELECT radiant, dire, radiant_win FROM matches "
                          "WHERE mode = ? AND duration >= ? AND start_time >= ?",
                          (ALL_PICK, valve_stats.MIN_DURATION, start))
        count = 0

        def matches():
            nonlocal count
            for radiant, dire, radiant_win in rows:
                count += 1
                yield [int(h) for h in radiant.split(",")], [int(h) for h in dire.split(",")], bool(radiant_win)

        hero, versus, _ = valve_stats.tables(matches())
    finally:
        db.close()
    matchups = {}
    for (a, b), cell in versus.items():
        matchups.setdefault(a, {})[b] = list(cell)
    return {"matchups": matchups, "base": {h: list(cell) for h, cell in hero.items()}, "matches": count,
            "from": start, "to": newest, "days": days, "built_at": time.time()}


def write(stats, path=FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def prune(days, db_path=valve_collect.DB_FILE):
    """Delete the matches older than `days` before the newest one (the file keeps its size, new matches
    reuse the freed space). Returns how many were deleted."""
    db = sqlite3.connect(db_path, timeout=60)
    try:
        newest = db.execute("SELECT MAX(start_time) FROM matches").fetchone()[0]
        if newest is None:
            return 0
        deleted = db.execute("DELETE FROM matches WHERE start_time < ?", (newest - days * 86400,)).rowcount
        db.commit()
        return deleted
    finally:
        db.close()


def make_seed(path, days=14, db_path=valve_collect.DB_FILE):
    """A copy of the database for another machine (the server): the matches of the last `days` days and
    the place where the collector stopped. One plain file. Stop the collector before copying, so the
    server goes on exactly where this one stopped. Returns how many matches were copied."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    valve_collect.open_db(path).close()
    db = sqlite3.connect(path, timeout=60)
    try:
        db.execute("ATTACH DATABASE ? AS src", (str(db_path),))
        newest = db.execute("SELECT MAX(start_time) FROM src.matches").fetchone()[0]
        if newest is None:
            raise ValueError("no matches stored yet")
        copied = db.execute("INSERT INTO main.matches SELECT * FROM src.matches WHERE start_time >= ?",
                            (newest - days * 86400,)).rowcount
        db.execute("INSERT OR REPLACE INTO main.state SELECT * FROM src.state")
        db.commit()
        db.execute("DETACH DATABASE src")
        db.execute("PRAGMA journal_mode=DELETE")  # no -wal / -shm files next to it: one file to upload
        return copied
    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--seed" in args:
        at = args.index("--seed")
        seed_days = int(args[at + 2]) if len(args) > at + 2 else 14
        print(f"copied {make_seed(args[at + 1], seed_days)} matches of the last {seed_days} days to {args[at + 1]}")
        raise SystemExit
    prune_days = None
    if "--prune" in args:
        at = args.index("--prune")
        prune_days = int(args[at + 1])
        del args[at:at + 2]
    target = Path(args[0]) if args else FILE
    days = int(args[1]) if len(args) > 1 else DAYS
    stats = build(days)
    write(stats, target)
    print(f"{stats['matches']} All Pick matches of the last {days} days "
          f"(until {time.strftime('%d.%m %H:%M', time.localtime(stats['to']))}), "
          f"{len(stats['base'])} heroes -> {target} ({target.stat().st_size // 1024} KB)")
    if prune_days:
        print(f"deleted {prune(prune_days)} matches older than {prune_days} days")
