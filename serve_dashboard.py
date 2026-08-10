#!/usr/bin/env python3
"""
Local server for dashboard.html.

Why this exists: the dashboard needs the Google Sheet, and a plain HTML file
opened from disk can't read it (that needs the service account, or else
publishing the tabs to the web). This server holds the credential, keeps it
server-side, and exposes a small read-only JSON API to the page on localhost.

It never calls Strava. The sync scripts already write everything into the
sheet, so the dashboard reads it back from there -- re-fetching from the API
would just be a slower way to get the same numbers, and would burn rate
limit on every page load.

Run it:
    pip install gspread google-auth
    python serve_dashboard.py

It reads config from environment variables, or from a `.env` file next to
this script (same KEY=value format, # comments allowed):

    GOOGLE_SHEET_ID             required -- the sheet the sync scripts write to
    GOOGLE_SERVICE_ACCOUNT_JSON optional -- defaults to ./service_account.json
    RACE_DATE                   optional -- default 2026-11-01 (NYC Marathon)
    FIRST_WEEK                  optional -- default 15. The training block
                                starts this many weeks out; anything earlier
                                is ignored by the dashboard entirely (table
                                and summary cards alike). Raise it if you
                                want more history in view.
    PORT                        optional -- default 8420

Add `.env` to .gitignore. It is not committed by this repo's .gitignore
by default, so do that before putting anything in it.

Endpoints (all read-only, bound to 127.0.0.1 only):
    GET /                      dashboard.html
    GET /api/config            race date, block start, whether the sheet is live
    GET /api/sheet             {activities: [...], laps: [...]} from the sheet

Flags:
    --demo    serve generated sample data instead of reading Google.
              Useful for checking the page renders without any credentials.
    --port N  override PORT
    --no-open don't open a browser
"""

import json
import os
import random
import sys
import threading
import time
import webbrowser
from datetime import date, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSHEET_TITLE = "STRAVA"
LAPS_WORKSHEET_TITLE = "Details"
DEFAULT_RACE_DATE = "2026-11-01"
SHEET_TTL = 120       # seconds to cache the sheet read

DEMO = "--demo" in sys.argv


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def env(name, default=None):
    """os.environ.get, but treats an empty/whitespace value as absent.

    GitHub Actions expands an undefined `${{ vars.X }}` to an empty string
    rather than omitting the variable, so a plain os.environ.get(name, default)
    hands back "" and the default never applies. That crashed the build on
    int("") for FIRST_WEEK, and would have silently produced an invalid
    RACE_DATE -- which the page turns into NaN weeks rather than an error."""
    v = os.environ.get(name)
    v = v.strip() if v else ""
    return v if v else default


def load_dotenv():
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            # Overwrite blanks too, so an empty CI variable can't shadow .env
            if not env(k):
                os.environ[k] = v.strip().strip("'\"")


load_dotenv()

RACE_DATE = env("RACE_DATE", DEFAULT_RACE_DATE)
FIRST_WEEK = int(env("FIRST_WEEK", "15"))
PORT = int(env("PORT", "8420"))
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])

HAVE_SHEET = DEMO or bool(env("GOOGLE_SHEET_ID"))

# Fail loudly here rather than shipping a snapshot the dashboard can't parse.
try:
    date.fromisoformat(RACE_DATE)
except ValueError:
    sys.exit(f"RACE_DATE must be YYYY-MM-DD, got {RACE_DATE!r}")
if not 1 <= FIRST_WEEK <= 104:
    sys.exit(f"FIRST_WEEK must be between 1 and 104, got {FIRST_WEEK}")


# --------------------------------------------------------------------------
# Google Sheet
# --------------------------------------------------------------------------
_sheet_cache = {"at": 0, "data": None}
_sheet_lock = threading.Lock()


def sheet_client():
    import gspread
    from google.oauth2.service_account import Credentials

    raw = env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        info = json.loads(raw)
    else:
        path = os.path.join(HERE, "service_account.json")
        if not os.path.exists(path):
            raise RuntimeError(
                "No service account credentials. Set GOOGLE_SERVICE_ACCOUNT_JSON "
                "or put service_account.json next to this script."
            )
        with open(path) as f:
            info = json.load(f)
    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )
    return gspread.authorize(creds)


def fetch_sheet():
    if DEMO:
        return demo_sheet()
    with _sheet_lock:
        if _sheet_cache["data"] and time.time() - _sheet_cache["at"] < SHEET_TTL:
            return _sheet_cache["data"]

        gc = sheet_client()
        sh = gc.open_by_key(env("GOOGLE_SHEET_ID"))

        def records(title):
            try:
                return sh.worksheet(title).get_all_records()
            except Exception as e:
                print(f"  ! couldn't read '{title}' tab: {e}")
                return []

        data = {"activities": records(WORKSHEET_TITLE), "laps": records(LAPS_WORKSHEET_TITLE)}
        _sheet_cache.update(at=time.time(), data=data)
        return data


# --------------------------------------------------------------------------
# demo data (--demo) -- lets the page be checked with no credentials at all
# --------------------------------------------------------------------------
def demo_sheet():
    random.seed(11)
    acts, laps, aid = [], [], 15_000_000
    start = date.fromisoformat(RACE_DATE) - timedelta(days=7 * 16)
    day = start
    while day <= date.today():
        if random.random() < 0.2:
            day += timedelta(days=1)
            continue
        aid += 1
        long_run = day.weekday() == 6
        mi = round(random.uniform(11, 20), 2) if long_run else round(random.uniform(3, 9), 2)
        pace_mi = round(random.uniform(7.1, 9.9), 2)
        mv = round(mi * pace_mi, 2)
        hr = round(random.uniform(132, 168), 1)
        cad = round(random.uniform(80, 91), 1)
        elev_ft = round(random.uniform(40, 700), 1)
        acts.append({
            "activity_id": aid, "date": day.isoformat(),
            "time": "07:%02d:00" % random.randint(0, 59),
            "name": random.choice(["Morning Run", "Long Run", "Tempo Session",
                                   "Recovery Jog", "Track Intervals"]),
            "sport_type": "Run", "description": "",
            "distance_km": round(mi * 1.60934, 3), "distance_mi": mi,
            "moving_time_min": mv, "elapsed_time_min": round(mv * 1.06, 2),
            "elevation_gain_m": round(elev_ft / 3.28084, 1), "elevation_gain_ft": elev_ft,
            "avg_pace_min_per_km": round(pace_mi / 1.60934, 2), "avg_pace_min_per_mi": pace_mi,
            "calories": random.randint(300, 1600), "avg_cadence": cad,
            "has_heartrate": "TRUE", "avg_heartrate": hr,
            "max_heartrate": round(hr + random.uniform(8, 26), 1),
            "avg_watts": "", "suffer_score": random.randint(20, 200),
            "gear_brand": "Nike", "gear_model": "Vaporfly 3",
        })
        for i in range(1, int(mi) + 1):
            lp = round(pace_mi + random.uniform(-0.8, 0.8), 2)
            lhr = round(hr + random.uniform(-12, 14), 1)
            laps.append({
                "activity_id": aid, "date": day.isoformat(), "lap_index": i,
                "lap_name": f"Lap {i}", "distance_km": 1.609, "distance_mi": 1.0,
                "moving_time_min": lp, "elapsed_time_min": round(lp * 1.02, 2),
                "elevation_gain_m": round(random.uniform(0, 30), 1),
                "elevation_gain_ft": round(random.uniform(0, 100), 1),
                "avg_pace_min_per_km": round(lp / 1.60934, 2), "avg_pace_min_per_mi": lp,
                "avg_heartrate": lhr, "max_heartrate": round(lhr + random.uniform(5, 20), 1),
                "avg_cadence": round(cad + random.uniform(-4, 4), 1), "avg_watts": "",
            })
        # a second run on some days, and an occasional non-run session, so the
        # same-day merge and the non-run display path are both exercised
        if random.random() < 0.18:
            aid += 1
            mi2 = round(random.uniform(2, 5), 2); pace2 = round(random.uniform(8, 10), 2)
            mv2 = round(mi2 * pace2, 2); hr2 = round(random.uniform(125, 150), 1)
            acts.append({
                "activity_id": aid, "date": day.isoformat(), "time": "18:%02d:00" % random.randint(0, 59),
                "name": "Evening Shakeout", "sport_type": "Run", "description": "",
                "distance_km": round(mi2 * 1.60934, 3), "distance_mi": mi2,
                "moving_time_min": mv2, "elapsed_time_min": round(mv2 * 1.05, 2),
                "elevation_gain_m": 12.0, "elevation_gain_ft": 39.4,
                "avg_pace_min_per_km": round(pace2 / 1.60934, 2), "avg_pace_min_per_mi": pace2,
                "calories": 300, "avg_cadence": 84.0, "has_heartrate": "TRUE",
                "avg_heartrate": hr2, "max_heartrate": round(hr2 + 12, 1),
                "avg_watts": "", "suffer_score": 30, "gear_brand": "Nike", "gear_model": "Pegasus",
            })
        if random.random() < 0.15:
            aid += 1
            mv3 = round(random.uniform(30, 75), 2); hr3 = round(random.uniform(110, 140), 1)
            acts.append({
                "activity_id": aid, "date": day.isoformat(), "time": "12:%02d:00" % random.randint(0, 59),
                "name": random.choice(["Recovery Spin", "Strength Session", "Pool Swim"]),
                "sport_type": random.choice(["Ride", "WeightTraining", "Swim"]), "description": "",
                "distance_km": "", "distance_mi": "",
                "moving_time_min": mv3, "elapsed_time_min": round(mv3 * 1.1, 2),
                "elevation_gain_m": "", "elevation_gain_ft": "",
                "avg_pace_min_per_km": "", "avg_pace_min_per_mi": "",
                "calories": 250, "avg_cadence": "", "has_heartrate": "TRUE",
                "avg_heartrate": hr3, "max_heartrate": round(hr3 + 18, 1),
                "avg_watts": "", "suffer_score": 25, "gear_brand": "", "gear_model": "",
            })
        day += timedelta(days=1)
    return {"activities": acts, "laps": laps}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "TrainingDashboard/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            print(f"  {args[0]}")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html", "/dashboard.html"):
                with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")

            if path == "/api/config":
                return self._send(200, {
                    "raceDate": RACE_DATE, "firstWeek": FIRST_WEEK, "demo": DEMO,
                    "haveSheet": HAVE_SHEET, "today": date.today().isoformat(),
                })

            if path == "/api/sheet":
                if not HAVE_SHEET:
                    return self._send(503, {"error": "GOOGLE_SHEET_ID is not set."})
                return self._send(200, fetch_sheet())

            return self._send(404, {"error": "not found"})

        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"  ! {path} -> {msg}")
            return self._send(502, {"error": msg})


def main():
    if not os.path.exists(os.path.join(HERE, "dashboard.html")):
        sys.exit("dashboard.html is missing from this folder.")

    print(f"\n  Training dashboard  ->  http://127.0.0.1:{PORT}/")
    if DEMO:
        print("  Mode: DEMO (generated data, no credentials used)")
    else:
        print(f"  Google Sheet: {'on' if HAVE_SHEET else 'OFF (set GOOGLE_SHEET_ID)'}")
    print(f"  Race date: {RACE_DATE}   Block starts at week {FIRST_WEEK}   Ctrl-C to stop\n")

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--no-open" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
