"""Hero statistics from the matches valve_collect.py stored: winrate, advantage against every enemy
(Dotabuff's formula, as in scoring.py) and synergy with every teammate - and how far they are from the
STRATZ numbers the program uses.

    python valve_stats.py          ranked All Pick (lobby 7, mode 22)
    python valve_stats.py --all    every public mode (Turbo, unranked All Pick...)
"""
import sqlite3
import sys

import valve_collect

RANKED, ALL_PICK_RANKED = 7, 22
MIN_DURATION = 600  # shorter games are remakes and early abandons


def load(db_path=valve_collect.DB_FILE, ranked_all_pick=True):
    """[(radiant heroes, dire heroes, radiant won)] of the stored matches."""
    db = sqlite3.connect(db_path)
    query = "SELECT radiant, dire, radiant_win FROM matches WHERE duration >= ?"
    params = [MIN_DURATION]
    if ranked_all_pick:
        query += " AND lobby = ? AND mode = ?"
        params += [RANKED, ALL_PICK_RANKED]
    rows = [([int(h) for h in r.split(",")], [int(h) for h in d.split(",")], bool(w))
            for r, d, w in db.execute(query, params)]
    db.close()
    return rows


def tables(matches):
    """games/wins of every hero, of every hero against every enemy and with every teammate."""
    hero, versus, together = {}, {}, {}

    def add(table, key, won):
        cell = table.setdefault(key, [0, 0])
        cell[0] += 1
        cell[1] += won

    for radiant, dire, radiant_won in matches:
        for team, enemies, won in ((radiant, dire, radiant_won), (dire, radiant, not radiant_won)):
            for h in team:
                add(hero, h, won)
                for e in enemies:
                    add(versus, (h, e), won)
                for t in team:
                    if t != h:
                        add(together, (h, t), won)
    return hero, versus, together


def advantage(hero, versus, a, b):
    """Dotabuff's "advantage" of a against b in points: winrate against b - (a's - b's winrate + 50%)."""
    games, wins = versus[(a, b)]
    return (wins / games - (hero[a][1] / hero[a][0] - hero[b][1] / hero[b][0] + 0.5)) * 100


def main():
    import data  # only the comparison with STRATZ needs these (the server has neither)
    import scoring
    ranked = "--all" not in sys.argv
    matches = load(ranked_all_pick=ranked)
    print(f"{len(matches)} matches ({'ranked All Pick' if ranked else 'all public modes'}, at least "
          f"{MIN_DURATION // 60} minutes)")
    if not matches:
        return
    hero, versus, together = tables(matches)
    stratz = data.load_cache()
    names = {i: h["name"] for i, h in stratz.heroes.items()}
    rows = sorted(((w / g, g, h) for h, (g, w) in hero.items()), reverse=True)
    print("best:", ", ".join(f"{names.get(h, h)} {wr:.1%} ({g})" for wr, g, h in rows[:5]))
    print("worst:", ", ".join(f"{names.get(h, h)} {wr:.1%} ({g})" for wr, g, h in rows[-5:]))
    for floor in (100, 1000):
        diffs = [abs(w / g - scoring.matchup_winrate(stratz, h)) * 100 for h, (g, w) in hero.items()
                 if g >= floor and h in stratz.heroes]
        if diffs:
            print(f"hero winrate vs STRATZ (5 weeks), heroes with {floor}+ games: {len(diffs)} heroes, "
                  f"mean difference {sum(diffs) / len(diffs):.2f} points")
    pairs = [abs(advantage(hero, versus, a, b) - scoring._counter(stratz, stratz.matchups[a][b], a, b))
             for (a, b), (g, _) in versus.items()
             if g >= 300 and stratz.matchups.get(a, {}).get(b, (0,))[0] >= scoring.MIN_PAIR_GAMES]
    if pairs:
        print(f"advantage vs STRATZ, pairs with 300+ games here: {len(pairs)} pairs, "
              f"mean difference {sum(pairs) / len(pairs):.2f} points")


if __name__ == "__main__":
    main()
