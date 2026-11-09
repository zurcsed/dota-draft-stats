"""How many people use the friends' exe. Once a day, when Dota 2 is running and talks to the helper, the
exe tells our server a random install number and its version. Nothing else is sent; the server keeps no IP
addresses, only (day, number, version). Waiting for Dota leaves out the antivirus sandboxes that start
every downloaded exe on a clean machine (v1.4 counted every start: 29 "installs" for 7 downloads).
The server also notes the GitHub download count a few times a day.

    count_dota_day(version)                 the exe side (main.py, on Dota's packets; never in the developer's copy)
    python3 launch_count.py --serve         the server side (launch-count.service, port 80)
    python3 launch_count.py --report [days] downloads and players per day (on the server; статистика.bat)
"""
import datetime
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paths

URL = "http://62.109.7.63/ping"
RELEASES_API = "https://api.github.com/repos/zurcsed/dota-draft-stats/releases?per_page=100"
ID_FILE = paths.CACHE / "install_id.txt"
DB = paths.CACHE / "launches.sqlite"
PORT = 80
DOWNLOADS_EVERY = 3 * 3600
FIRST_DOTA_VERSION = (1, 5)  # older versions counted every start, sandboxes too
ID_RE = re.compile(r"[0-9a-f]{32}")
VERSION_RE = re.compile(r"\d+(\.\d+){0,3}")

_counted_day = None
_lock = threading.Lock()


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def install_id():
    """A random number made on the first start and kept in the user's own folder (it says nothing about them)."""
    try:
        saved = ID_FILE.read_text(encoding="ascii").strip()
        if ID_RE.fullmatch(saved):
            return saved
    except OSError:
        pass
    new = uuid.uuid4().hex
    ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    ID_FILE.write_text(new, encoding="ascii")
    return new


def ping(version):
    """Count this day; any failure (no network, server down) is ignored - the helper does not depend on it."""
    try:
        query = urllib.parse.urlencode({"id": install_id(), "v": version})
        with urllib.request.urlopen(f"{URL}?{query}", timeout=10):
            pass
    except Exception:
        pass


def count_dota_day(version):
    """Called on every packet from Dota: the first one of a day sends the ping, in the background."""
    global _counted_day
    with _lock:
        if _counted_day == _today():
            return
        _counted_day = _today()
    threading.Thread(target=ping, args=(version,), daemon=True).start()


def _numbers(version):
    return tuple(int(part) for part in version.split("."))


def _open(db):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS launches (day TEXT, id TEXT, version TEXT, PRIMARY KEY (day, id))")
    con.execute("CREATE TABLE IF NOT EXISTS downloads (day TEXT PRIMARY KEY, total INTEGER)")
    return con


def _write(db, sql, values):
    con = _open(db)
    try:
        with con:
            con.execute(sql, values)
    finally:
        con.close()


def record(db, install, version, day=None):
    """Store one day of one install; False for anything that is not a well-formed number and version."""
    if not (ID_RE.fullmatch(install or "") and VERSION_RE.fullmatch(version or "")):
        return False
    _write(db, "INSERT OR REPLACE INTO launches VALUES (?, ?, ?)", (day or _today(), install, version))
    return True


def record_downloads(db, total, day=None):
    _write(db, "INSERT OR REPLACE INTO downloads VALUES (?, ?)", (day or _today(), int(total)))


def github_downloads():
    """All downloads of all releases' files on GitHub (the raw link in the README is not counted by GitHub)."""
    request = urllib.request.Request(RELEASES_API, headers={"User-Agent": "dota-draft-helper-server",
                                                            "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return sum(asset["download_count"] for release in json.load(resp) for asset in release["assets"])


def report(db, days=14, today=None):
    """Lines in Russian for the developer: per day downloads, players with the helper and new ones."""
    con = _open(db)
    try:
        launches = con.execute("SELECT day, id, version FROM launches ORDER BY day").fetchall()
        downloads = dict(con.execute("SELECT day, total FROM downloads").fetchall())
    finally:
        con.close()
    played = [(day, install) for day, install, version in launches if _numbers(version) >= FIRST_DOTA_VERSION]
    first_day = {}
    for day, install in played:
        first_day.setdefault(install, day)
    end = datetime.date.fromisoformat(today or _today())
    last_days = [(end - datetime.timedelta(days=n)).isoformat() for n in range(days)]
    week = set(last_days[:7])

    def downloads_on(day):
        before = [total for d, total in downloads.items() if d < day]
        if day not in downloads or not before:
            return "—"
        return str(downloads[day] - max(before))

    lines = [f"Скачиваний exe с GitHub всего: {max(downloads.values()) if downloads else '—'}"
             " (по ссылке raw из README GitHub не считает)", "",
             "дата         скачали  играли с подсказчиком  из них впервые"]
    for day in last_days:
        players = {install for d, install in played if d == day}
        new = sum(1 for install in players if first_day[install] == day)
        lines.append(f"{day}  {downloads_on(day):>7}  {len(players):>21}  {new:>14}")
    lines += ["",
              f"Играли за последние 7 дней: {len({i for d, i in played if d in week})}",
              f"Всего играли хоть раз: {len(first_day)}",
              "Запуски старых версий (до v1.5, вместе с проверками антивирусов) за 7 дней: "
              f"{len({i for d, i, v in launches if d in week and _numbers(v) < FIRST_DOTA_VERSION})}",
              "«Играли» — запущенная Dota 2 подключилась к подсказчику (с v1.5; старые версии здесь не видны)."]
    return lines


class _Handler(BaseHTTPRequestHandler):
    db = DB

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        ok = url.path == "/ping" and record(self.db, query.get("id", [""])[0], query.get("v", [""])[0])
        self.send_response(204 if ok else 404)
        self.send_header("Content-Length", "0")  # without it a proxy on the way waits for a body
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, *args):  # the default log has the IP address: keep nothing
        pass


def _note_downloads(db):
    while True:
        try:
            record_downloads(db, github_downloads())
        except Exception as e:  # GitHub down or its limit: next time
            print(f"downloads: {e}", flush=True)
        time.sleep(DOWNLOADS_EVERY)


def serve(port=PORT, db=DB):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    _Handler.db = db
    threading.Thread(target=_note_downloads, args=(db,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve"]:
        serve()
    elif sys.argv[1:2] == ["--report"]:
        print("\n".join(report(DB, int(sys.argv[2]) if len(sys.argv) > 2 else 14)))
    else:
        print(__doc__)
