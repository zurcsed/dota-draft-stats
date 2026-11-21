"""Build a seven-day draft model from a read-only Valve database or a compressed match export."""
import argparse
import gzip
import json
import math
import random
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import valve_collect
import valve_stats
from draft_prediction import DraftPrediction, FORMAT, MODEL_FILE, WEEK, load_weekly, parse, probability

DAY = 86400
MAX_LAG = 36 * 3600  # the same freshness limit used to check the legacy Valve feed
CALIBRATION_STRIDE = 4
ARCHIVE = MODEL_FILE.with_name("draft_week_matches.tsv.gz")


def matches(db, start, end, stride, ranked_only):
    rows = db.execute(
        "SELECT radiant, dire, radiant_win FROM matches "
        "WHERE (? = 0 OR lobby = ?) AND mode = ? AND duration >= ? "
        "AND start_time >= ? AND start_time < ? AND match_id % ? = 0",
        (ranked_only, valve_stats.RANKED, valve_stats.ALL_PICK_RANKED, valve_stats.MIN_DURATION,
         start, end, stride))
    for radiant, dire, won in rows:
        yield [int(hero) for hero in radiant.split(",")], [int(hero) for hero in dire.split(",")], won


def archive_matches(path, start, end, stride, ranked_only):
    with gzip.open(path, "rt", encoding="ascii") as archive:
        next(archive)  # exclusive end timestamp, also written by the server export
        for line in archive:
            match_id, started, lobby, won, radiant, dire = line.strip().split("\t")
            if start <= int(started) < end and int(match_id) % stride == 0 and (not ranked_only or lobby == "7"):
                yield [int(hero) for hero in radiant.split(",")], [int(hero) for hero in dire.split(",")], int(won)


def examples(pairs, games, seed):
    rng = random.Random(seed)
    result = []
    for radiant, dire, won in games:
        # Exercise unfinished drafts as well as complete ones. Sample visible picks
        # without assuming that the order in the match database is the pick order.
        if rng.randrange(2):
            radiant = rng.sample(radiant, rng.randint(1, len(radiant)))
            dire = rng.sample(dire, rng.randint(1, len(dire)))
        score = pairs.draft_strength(radiant, dire)
        if score is not None:
            result.append((score, won))
    return result


def fit(rows):
    """Two-parameter logistic regression using Newton updates."""
    intercept, strength = 0.0, 0.0
    for _ in range(12):
        error, weighted_error = 0.0, 0.0
        h00, h01, h11 = 0.0, 0.0, 0.0
        for score, won in rows:
            predicted = probability(intercept + strength * score)
            residual = won - predicted
            weight = predicted * (1 - predicted)
            error += residual
            weighted_error += residual * score
            h00 += weight
            h01 += weight * score
            h11 += weight * score * score
        determinant = h00 * h11 - h01 * h01
        if determinant <= 0:
            raise ValueError("Not enough independent matches to fit a prediction")
        change_intercept = (h11 * error - h01 * weighted_error) / determinant
        change_strength = (h00 * weighted_error - h01 * error) / determinant
        intercept += change_intercept
        strength += change_strength
        if abs(change_intercept) + abs(change_strength) < 1e-7:
            break
    return intercept, strength


def loss(rows, intercept, strength):
    return sum(-math.log(max(1e-15, probability((2 * won - 1) * (intercept + strength * score))))
               for score, won in rows) / len(rows)


def build(end, games_in_range, ranked_only):
    start, pair_end, fit_end = end - WEEK, end - 2 * DAY, end - DAY
    end_date = datetime.fromtimestamp(end - 1, timezone.utc).date().isoformat()
    # Keep the last two days independent for calibration and a chronological check.
    print("Building training pair tables", flush=True)
    _, matchups, synergy = valve_stats.tables(games_in_range(start, pair_end, CALIBRATION_STRIDE, ranked_only))
    pairs = DraftPrediction(matchups, synergy, 0, 0, end_date)
    train = examples(pairs, games_in_range(pair_end, fit_end, CALIBRATION_STRIDE, ranked_only), seed=1)
    print(f"Calibration matches: {len(train):,}", flush=True)
    intercept, strength = fit(train)
    prior = sum(won for _, won in train) / len(train)
    del train
    test = examples(pairs, games_in_range(fit_end, end, CALIBRATION_STRIDE, ranked_only), seed=2)
    if len(test) < 1000 or not math.isfinite(strength) or strength <= 0:
        raise ValueError("Not enough validation data or the draft signal is reversed")
    baseline = loss(test, math.log(prior / (1 - prior)), 0)
    actual = loss(test, intercept, strength)
    print(f"Held out: {len(test):,}; log loss {actual:.4f} vs {baseline:.4f} for side alone")
    if actual >= baseline:
        raise ValueError("Draft prediction did not improve on side alone")
    # Once checked, use all seven days in the displayed pair tables.
    print("Building the full seven-day tables", flush=True)
    base, matchups, synergy = valve_stats.tables(games_in_range(start, end, 1, ranked_only))
    count = sum(row[0] for row in base.values()) // 10
    return {"format": FORMAT, "fetched_at": end, "end_date": end_date, "period": [start, end], "games": count,
            "ranked_only": ranked_only, "coefficients": [intercept, strength],
            "base": [[hero, *row] for hero, row in base.items()],
            "matchups": [[*pair, *row] for pair, row in matchups.items()],
            "synergy": [[*pair, *row] for pair, row in synergy.items()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--archive", type=Path)
    source.add_argument("--db", type=Path)
    parser.add_argument("--daily", action="store_true", help="publish only a fresh, complete window from the Valve DB")
    parser.add_argument("--ranked-only", action="store_true")
    parser.add_argument("--output", type=Path, default=MODEL_FILE)
    args = parser.parse_args()
    if args.daily and (args.archive or args.ranked_only):
        parser.error("--daily requires the Valve DB and all All Pick matches")
    archive = args.archive or (ARCHIVE if not args.daily and not args.db and ARCHIVE.exists() else None)
    if archive:
        with gzip.open(archive, "rt", encoding="ascii") as source:
            end = int(next(source))
        model = build(end, lambda *bounds: archive_matches(archive, *bounds), args.ranked_only)
    else:
        source = (args.db or valve_collect.DB_FILE).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source, uri=True, timeout=60)) as db:
            db.execute("BEGIN")  # keep all four reads on one WAL snapshot while the collector runs
            oldest, newest = db.execute(
                "SELECT MIN(start_time), MAX(start_time) FROM matches "
                "WHERE mode = ? AND duration >= ? AND (? = 0 OR lobby = ?)",
                (valve_stats.ALL_PICK_RANKED, valve_stats.MIN_DURATION, args.ranked_only, valve_stats.RANKED)
            ).fetchone()
            if newest is None:
                raise ValueError("The Valve database has no eligible All Pick matches")
            end = newest + 1
            if args.daily and (oldest > end - WEEK or not -300 <= time.time() - end <= MAX_LAG):
                raise ValueError("The Valve database does not cover a fresh seven-day window")
            if args.daily:
                published = load_weekly(args.output)
                if published and published.fetched_at >= end:
                    print("The published draft model already covers this database snapshot")
                    return
            model = build(end, lambda *bounds: matches(db, *bounds), args.ranked_only)
    if not parse(model, weekly=not args.ranked_only):
        raise ValueError("The generated draft model is invalid")
    target = args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix(".tmp")
    pending.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    pending.replace(target)
    print(f"Saved {target.name}: {model['games']:,} All Pick matches; end (UTC): {model['end_date']}")


if __name__ == "__main__":
    main()
