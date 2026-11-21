"""Draft-only win estimate and recommendations from weekly Valve pair results."""
import json
import math
from datetime import datetime, timezone
from itertools import combinations, product
from pathlib import Path

import paths
FILENAME = "draft_prediction.json"
FORMAT = 1
MODEL_FILE = paths.CACHE / FILENAME
BUNDLED_FILE = paths.BUNDLED / "data" / FILENAME
PAIR_PRIOR = 30
WEEK = 7 * 86400


def probability(log_odds):
    return 1 / (1 + math.exp(-max(-60, min(60, log_odds))))


def pair_score(row):
    games, wins = row
    return (wins - games / 2) / (games + PAIR_PRIOR)


class DraftPrediction:
    def __init__(self, matchups, synergy, intercept, strength, end_date, *, base=None, period=None, games=None):
        self.matchups = matchups
        self.synergy = synergy
        self.intercept = intercept
        self.strength = strength
        self.end_date = end_date
        self.base = base or {}
        self.period = period
        self.fetched_at = period[1] if period else 0
        self.games = games
        self.winrates = {hero: wins / count for hero, (count, wins) in self.base.items() if count}

    def draft_strength(self, radiant, dire):
        """Difference between the two teams' synergy plus matchup scores."""
        radiant_synergy = [self.synergy[pair] for pair in combinations(radiant, 2) if pair in self.synergy]
        dire_synergy = [self.synergy[pair] for pair in combinations(dire, 2) if pair in self.synergy]
        matchups = [self.matchups[pair] for pair in product(radiant, dire) if pair in self.matchups]
        if not (radiant_synergy or dire_synergy or matchups):
            return None
        return (sum(map(pair_score, radiant_synergy)) - sum(map(pair_score, dire_synergy))
                + 2 * sum(map(pair_score, matchups)))

    def predict(self, radiant, dire):
        advantage = self.draft_strength(radiant, dire)
        if advantage is None:
            return None
        return probability(self.intercept + self.strength * advantage)

    def recommend(self, data, allies, enemies, allowed, unavailable, top):
        from scoring import MIN_GAMES, Recommendation

        picks = []
        for hero in allowed - unavailable:
            if not (allies or enemies):
                games, wins = self.base.get(hero, (0, 0))
                if games >= MIN_GAMES:
                    picks.append(Recommendation(hero, data.name(hero), wins / games, games))
                continue
            counters = self._reasons(self.matchups, hero, enemies, data, adjusted=bool(self.base))
            synergies = self._reasons(self.synergy, hero, allies, data)
            if counters or synergies:
                counter = sum(row[0] for row in counters) if counters else None
                score = (counter or 0) + sum(row[0] for row in synergies)
                games = sum(row[2] for row in counters + synergies)
                picks.append(Recommendation(hero, data.name(hero), score, games, counters,
                                            counter, synergies=synergies))
        return sorted(picks, key=lambda pick: (-pick.score, -pick.games, pick.hero_id))[:top]

    def _reasons(self, table, hero, others, data, *, adjusted=False):
        from scoring import MIN_PAIR_GAMES, counter_advantage

        reasons = []
        for other in sorted(others):
            row = table.get((hero, other))
            if row and (not adjusted or row[0] >= MIN_PAIR_GAMES):
                games, wins = row
                winrate = wins / games
                score = (counter_advantage(winrate, self.winrates[hero], self.winrates[other])
                         if adjusted else pair_score(row) * 100)
                reasons.append((score, data.name(other), games, winrate))
        return sorted(reasons, reverse=True)


def _counts(rows, pair=False):
    result = {}
    size = 4 if pair else 3
    for row in rows:
        if len(row) != size or any(type(value) is not int for value in row):
            raise ValueError("Invalid draft counts")
        *heroes, games, wins = row
        key = tuple(heroes) if pair else heroes[0]
        if min(heroes) <= 0 or len(set(heroes)) != len(heroes) or not 0 <= wins <= games or games == 0 or key in result:
            raise ValueError("Invalid draft counts")
        result[key] = (games, wins)
    return result


def parse(raw, *, weekly=False):
    """Read a model. Published files must describe a complete seven-day All Pick window."""
    try:
        if raw["format"] != FORMAT:
            return None
        matchups = _counts(raw["matchups"], pair=True)
        synergy = _counts(raw["synergy"], pair=True)
        intercept, strength = map(float, raw["coefficients"])
        if not all(map(math.isfinite, (intercept, strength))) or strength <= 0 or not matchups or not synergy:
            return None
        base = _counts(raw.get("base", []))
        period = raw.get("period")
        games = raw.get("games")
        end_date = raw["end_date"]
        if period is not None:
            start, end = period
            if (type(start) is not int or type(end) is not int or start < 0 or end - start != WEEK
                    or type(games) is not int or games <= 0 or sum(n for n, _ in base.values()) != games * 10
                    or end_date != datetime.fromtimestamp(end - 1, timezone.utc).date().isoformat()):
                return None
        if weekly and (period is None or raw.get("ranked_only") is not False
                       or type(raw.get("fetched_at")) is not int or raw["fetched_at"] != period[1]):
            return None
        if base and any(h not in base or other not in base for h, other in matchups):
            return None
        return DraftPrediction(matchups, synergy, intercept, strength, end_date, base=base, period=period, games=games)
    except (ValueError, KeyError, TypeError, OSError, OverflowError):
        return None


def _load(path, *, weekly=False):
    try:
        return parse(json.loads(Path(path).read_text(encoding="utf-8")), weekly=weekly)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_weekly(path):
    """Validate the specific file supplied by the downloader; never use a fallback here."""
    return _load(path, weekly=True)


def load(path=None):
    if path is not None:
        return _load(path)
    if not paths.FROZEN:
        return _load(MODEL_FILE)
    local, bundled = load_weekly(MODEL_FILE), load_weekly(BUNDLED_FILE)
    return max(filter(None, (local, bundled)), key=lambda model: model.fetched_at, default=None)
