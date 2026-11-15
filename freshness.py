"""Dates shown to players, independent of the most recent download attempt."""
import time


def describe(label, fetched_at, max_age, error=None, now=None):
    now = time.time() if now is None else now
    if not isinstance(fetched_at, (int, float)) or fetched_at <= 0:
        return f"{label}: нет даты данных", True
    old = now - fetched_at > max_age
    date = time.strftime("%d.%m %H:%M", time.localtime(fetched_at))
    note = " · обновление недоступно" if error else " · устарели" if old else ""
    return f"{label}: {date}{note}", old or bool(error)


def pro_summary(raw, now=None):
    now = time.time() if now is None else now
    rows = [entry for positions in (raw or {}).get("heroes", {}).values() for entry in positions.values()]
    stale = sum(now - entry.get("fetched_at", 0) > 8 * 86400 for entry in rows)
    return f"Раскачки: {len(rows)} позиций, устарели {stale}"
