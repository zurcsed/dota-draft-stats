"""How many people use the friends' exe: once a day it tells our server a random install number and its
version. Nothing else is sent; the server keeps no IP addresses, only (day, number, version).

    ping()                                  the exe side (main.py, once a day; never in the developer's copy)
    python3 launch_count.py --serve         the server side (launch-count.service, port 80)
    python3 launch_count.py --report [days] how many installs ran the helper each day (on the server)
"""
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paths

URL = "http://62.109.7.63/ping"
ID_FILE = paths.CACHE / "install_id.txt"
DB = paths.CACHE / "launches.sqlite"
PORT = 80
ID_RE = re.compile(r"[0-9a-f]{32}")
VERSION_RE = re.compile(r"\d+(\.\d+){0,3}")


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
    """Count this start; any failure (no network, server down) is ignored - the helper does not depend on it."""
    query = urllib.parse.urlencode({"id": install_id(), "v": version})
    try:
        with urllib.request.urlopen(f"{URL}?{query}", timeout=10):
            pass
    except Exception:
        pass


def _open(db):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS launches (day TEXT, id TEXT, version TEXT, PRIMARY KEY (day, id))")
    return con


def record(db, install, version, day=None):
    """Store one start; False for anything that is not a well-formed number and version."""
    if not (ID_RE.fullmatch(install or "") and VERSION_RE.fullmatch(version or "")):
        return False
    day = day or time.strftime("%Y-%m-%d", time.gmtime())
    con = _open(db)
    try:
        with con:
            con.execute("INSERT OR REPLACE INTO launches VALUES (?, ?, ?)", (day, install, version))
    finally:
        con.close()
    return True


def report(db, days=14):
    """Lines: installs per day for the last days, installs ever, and which versions they run now."""
    con = _open(db)
    try:
        per_day = con.execute("SELECT day, COUNT(*) FROM launches GROUP BY day ORDER BY day DESC LIMIT ?",
                              (days,)).fetchall()
        ever = con.execute("SELECT COUNT(DISTINCT id) FROM launches").fetchone()[0]
        versions = con.execute("SELECT version, COUNT(*) FROM (SELECT id, version, MAX(day) FROM launches"
                               " GROUP BY id) GROUP BY version ORDER BY version DESC").fetchall()
    finally:
        con.close()
    lines = [f"{day}: {count}" for day, count in per_day]
    lines.append(f"installs ever: {ever}")
    lines.append("latest version per install: " + (", ".join(f"v{v} {n}" for v, n in versions) or "none"))
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


def serve(port=PORT, db=DB):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    _Handler.db = db
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve"]:
        serve()
    elif sys.argv[1:2] == ["--report"]:
        print("\n".join(report(DB, int(sys.argv[2]) if len(sys.argv) > 2 else 14)))
    else:
        print(__doc__)
