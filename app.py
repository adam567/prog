"""PH3 Tracker server: serves the PWA and stores its data in SQLite.

Standard library only (no pip install), so App Service starts it instantly.

Data model: the app keeps a few JSON documents (profile, program, workouts,
cardio, ...). Each PUT replaces one document if it is newer than what the
server has (last write wins) and every write is also appended to a history
table, so nothing is ever lost even if a stale phone overwrites newer data.

Strava: optional. When STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET are set, the
app can connect once via OAuth; the server then fetches activities and their
heart-rate streams and returns a per-bpm histogram so the app can compute
minutes in Zone 2 for whatever range is configured.
"""
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PH3_DB") or os.path.join(ROOT, "data", "ph3.db")
API_KEY = os.environ.get("PH3_KEY", "")
STRAVA_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = 5 * 1024 * 1024
KEY_RE = re.compile(r"^[A-Za-z]{1,32}$")
NO_HISTORY = {"activeWorkout"}  # changes every set; not worth versioning

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}

_db_lock = threading.Lock()


def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock, db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS docs (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS history (
              id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
              value TEXT NOT NULL, updated INTEGER NOT NULL, received INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strava_hr (
              id INTEGER PRIMARY KEY, hist TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strava_series (
              id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            """
        )


def meta_get(k):
    with db() as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else None


def meta_set(k, v):
    with _db_lock, db() as c:
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, json.dumps(v)))


# ---------------------------------------------------------------- Strava
def _http_json(url, data=None, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    with urllib.request.urlopen(req, data=body, timeout=20) as r:
        return json.loads(r.read().decode())


def strava_token():
    tok = meta_get("strava_tokens")
    if not tok:
        return None
    if tok["expires_at"] < time.time() + 120:
        new = _http_json(
            "https://www.strava.com/oauth/token",
            {"client_id": STRAVA_ID, "client_secret": STRAVA_SECRET,
             "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
        )
        tok.update({k: new[k] for k in ("access_token", "refresh_token", "expires_at")})
        meta_set("strava_tokens", tok)
    return tok["access_token"]


def hr_histogram(stream):
    """Seconds spent at each bpm. Gaps over 10 s (pauses) count as 10 s."""
    hr = stream.get("heartrate", {}).get("data") or []
    t = stream.get("time", {}).get("data") or []
    hist = {}
    for i in range(1, min(len(hr), len(t))):
        dt = min(max(t[i] - t[i - 1], 0), 10)
        if dt and hr[i]:
            hist[int(hr[i])] = hist.get(int(hr[i]), 0) + dt
    return hist


GYM_SPORTS = {"WeightTraining", "Workout", "Crossfit"}


def save_series(act_id, stream):
    t = stream.get("time", {}).get("data") or []
    hr = stream.get("heartrate", {}).get("data") or []
    n = min(len(t), len(hr))
    data = json.dumps({"t": t[:n], "hr": hr[:n]})
    with _db_lock, db() as c:
        c.execute("INSERT OR REPLACE INTO strava_series(id,data) VALUES(?,?)", (act_id, data))
    return data


def strava_series(act_id):
    """Second-by-second heart rate for one activity (cached after first fetch)."""
    with db() as c:
        r = c.execute("SELECT data FROM strava_series WHERE id=?", (act_id,)).fetchone()
    if r:
        return json.loads(r["data"])
    token = strava_token()
    if not token:
        return {"error": "not connected"}
    s = _http_json(f"https://www.strava.com/api/v3/activities/{act_id}/streams?"
                   "keys=heartrate,time&key_by_type=true", token=token)
    return json.loads(save_series(act_id, s))


def strava_terrain(act_id):
    """Full streams for one activity (time, distance, altitude, grade, latlng,
    heart rate) for terrain-vs-HR analysis. Not cached."""
    token = strava_token()
    if not token:
        return {"error": "not connected"}
    s = _http_json(f"https://www.strava.com/api/v3/activities/{act_id}/streams?"
                   "keys=time,distance,altitude,grade_smooth,latlng,heartrate,velocity_smooth"
                   "&key_by_type=true", token=token)
    return {k: v.get("data") for k, v in s.items()}


def strava_sync(after):
    token = strava_token()
    if not token:
        return {"error": "not connected"}
    acts = _http_json(
        "https://www.strava.com/api/v3/athlete/activities?"
        + urllib.parse.urlencode({"after": int(after), "per_page": 60}),
        token=token,
    )
    out, fetched = [], 0
    for a in acts:
        hist = None
        if a.get("has_heartrate"):
            with db() as c:
                r = c.execute("SELECT hist FROM strava_hr WHERE id=?", (a["id"],)).fetchone()
            if r:
                hist = json.loads(r["hist"])
            elif fetched < 40:  # stay well inside Strava's 100 requests / 15 min
                fetched += 1
                try:
                    s = _http_json(
                        f"https://www.strava.com/api/v3/activities/{a['id']}/streams?"
                        "keys=heartrate,time&key_by_type=true", token=token)
                    hist = hr_histogram(s)
                    if (a.get("sport_type") or a.get("type")) in GYM_SPORTS:
                        save_series(a["id"], s)  # keep the full trace for set-by-set analysis
                    with _db_lock, db() as c:
                        c.execute("INSERT OR REPLACE INTO strava_hr(id,hist) VALUES(?,?)",
                                  (a["id"], json.dumps(hist)))
                except urllib.error.HTTPError:
                    hist = None
        out.append({
            "id": a["id"], "name": a.get("name", ""),
            "sport": a.get("sport_type") or a.get("type"),
            "start": a.get("start_date_local"),
            "startUtc": a.get("start_date"),
            "moving": a.get("moving_time", 0), "elapsed": a.get("elapsed_time", 0),
            "distance": a.get("distance", 0), "elev": a.get("total_elevation_gain", 0),
            "avgHr": a.get("average_heartrate"), "maxHr": a.get("max_heartrate"),
            "hist": hist,
        })
    return {"activities": out, "pendingHr": sum(1 for a in out if a["hist"] is None and a["avgHr"])}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "PH3"

    def log_message(self, fmt, *args):  # keep container logs short
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self):
        # Open by design (Adam's choice: obscurity is enough for workout logs).
        # Setting the PH3_KEY app setting turns the key check back on.
        if not API_KEY:
            return True
        got = self.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode(), ("Bearer " + API_KEY).encode()):
            self._send(401, {"error": "bad key"})
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _base_url(self):
        return "https://" + self.headers.get("Host", "ph3.azurewebsites.net")

    # -- GET
    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                with open(os.path.join(ROOT, name), "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                return self._send(404, {"error": "not found"})
            return self._send(200, data, ctype, {"Cache-Control": "no-cache"})
        if path == "/api/health":
            return self._send(200, {"ok": True})
        if path == "/api/strava/callback":
            return self._strava_callback(urllib.parse.parse_qs(qs))
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return
        if path == "/api/data":
            with db() as c:
                rows = c.execute("SELECT key,value,updated FROM docs").fetchall()
            return self._send(200, {r["key"]: {"value": json.loads(r["value"]), "updated": r["updated"]} for r in rows})
        if path.startswith("/api/strava/terrain/"):
            act = path.rsplit("/", 1)[1]
            if not act.isdigit():
                return self._send(400, {"error": "bad id"})
            try:
                return self._send(200, strava_terrain(int(act)))
            except urllib.error.HTTPError as e:
                return self._send(502, {"error": f"Strava returned {e.code}"})
            except (urllib.error.URLError, TimeoutError):
                return self._send(504, {"error": "Strava unreachable"})
        if path.startswith("/api/strava/series/"):
            act = path.rsplit("/", 1)[1]
            if not act.isdigit():
                return self._send(400, {"error": "bad id"})
            try:
                return self._send(200, strava_series(int(act)))
            except urllib.error.HTTPError as e:
                return self._send(502, {"error": f"Strava returned {e.code}"})
            except (urllib.error.URLError, TimeoutError):
                return self._send(504, {"error": "Strava unreachable"})
        if path == "/api/strava/status":
            tok = meta_get("strava_tokens")
            return self._send(200, {"configured": bool(STRAVA_ID and STRAVA_SECRET),
                                    "connected": bool(tok),
                                    "athlete": (tok or {}).get("athlete")})
        return self._send(404, {"error": "not found"})

    do_HEAD = do_GET

    # -- PUT /api/data/<key>
    def do_PUT(self):
        if not self.path.startswith("/api/data/"):
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return
        key = self.path[len("/api/data/"):]
        if not KEY_RE.match(key):
            return self._send(400, {"error": "bad key name"})
        try:
            body = self._body()
            updated = int(body["updated"])
            value = json.dumps(body["value"])
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "bad body"})
        now = int(time.time() * 1000)
        with _db_lock, db() as c:
            cur = c.execute("SELECT updated FROM docs WHERE key=?", (key,)).fetchone()
            if key not in NO_HISTORY:
                c.execute("INSERT INTO history(key,value,updated,received) VALUES(?,?,?,?)",
                          (key, value, updated, now))
            if cur and cur["updated"] > updated:
                return self._send(409, {"error": "server has newer", "updated": cur["updated"]})
            c.execute("INSERT OR REPLACE INTO docs(key,value,updated) VALUES(?,?,?)",
                      (key, value, updated))
        return self._send(200, {"ok": True, "updated": updated})

    # -- POST
    def do_POST(self):
        path = self.path.partition("?")[0]
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return
        if path == "/api/strava/start":
            if not (STRAVA_ID and STRAVA_SECRET):
                return self._send(400, {"error": "Strava app not configured on server"})
            state = secrets.token_urlsafe(24)
            meta_set("strava_state", {"state": state, "at": time.time()})
            url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
                "client_id": STRAVA_ID, "response_type": "code",
                "redirect_uri": self._base_url() + "/api/strava/callback",
                "approval_prompt": "auto", "scope": "read,activity:read_all", "state": state})
            return self._send(200, {"url": url})
        if path == "/api/strava/sync":
            try:
                body = self._body()
                return self._send(200, strava_sync(body.get("after", time.time() - 30 * 86400)))
            except urllib.error.HTTPError as e:
                return self._send(502, {"error": f"Strava returned {e.code}"})
            except (urllib.error.URLError, TimeoutError):
                return self._send(504, {"error": "Strava unreachable"})
        if path == "/api/strava/disconnect":
            with _db_lock, db() as c:
                c.execute("DELETE FROM meta WHERE k='strava_tokens'")
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def _strava_callback(self, q):
        saved = meta_get("strava_state") or {}
        state = (q.get("state") or [""])[0]
        ok = (saved.get("state") and hmac.compare_digest(state, saved["state"])
              and time.time() - saved.get("at", 0) < 900)
        meta_set("strava_state", {})
        if not ok or "code" not in q:
            return self._send(302, b"", "text/plain", {"Location": "/#strava=failed"})
        try:
            tok = _http_json("https://www.strava.com/oauth/token", {
                "client_id": STRAVA_ID, "client_secret": STRAVA_SECRET,
                "code": q["code"][0], "grant_type": "authorization_code"})
        except urllib.error.URLError:
            return self._send(302, b"", "text/plain", {"Location": "/#strava=failed"})
        ath = tok.get("athlete") or {}
        meta_set("strava_tokens", {
            "access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
            "expires_at": tok["expires_at"],
            "athlete": (ath.get("firstname", "") + " " + ath.get("lastname", "")).strip()})
        return self._send(302, b"", "text/plain", {"Location": "/#strava=connected"})


if __name__ == "__main__":
    init_db()
    print(f"PH3 listening on {PORT}, db={DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
