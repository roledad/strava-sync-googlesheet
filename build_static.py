#!/usr/bin/env python3
"""
Build a static, credential-free snapshot of the dashboard into ./public/.

    public/index.html   copy of dashboard.html (unchanged)
    public/data.json    the two sheet tabs, trimmed to the columns the page uses

`dashboard.html` looks for `data.json` next to itself on load. If it finds
one it runs fully client-side; if not it falls back to the /api/sheet
endpoint that serve_dashboard.py provides. So the same file works locally
against the live sheet and on a static host against this snapshot -- there is
only one copy of the dashboard to maintain.

This reads ONLY the Google Sheet. It makes no Strava calls: strava_to_sheets.py
has already written everything into the sheet by the time this runs, so
hitting the API again would be a slower way to get identical numbers, and
would spend rate limit on every build. The dashboard's "recent activities"
section is just a 10-day window over the same rows.

Run in CI after the sheet sync:

    python build_static.py

Needs GOOGLE_SHEET_ID plus service account credentials (env
GOOGLE_SERVICE_ACCOUNT_JSON, or ./service_account.json). Optional RACE_DATE
and FIRST_WEEK are copied into the snapshot so the page doesn't need them
configured separately. Pass --demo to build from generated data with no
credentials at all.

WHAT ENDS UP PUBLIC
    Everything in data.json is world-readable once deployed. Only the columns
    the dashboard renders are copied (ACT_FIELDS / LAP_FIELDS below), so
    nothing extra leaks by accident, but activity names, dates, distances,
    paces, heart rates and cadences ARE public. No tokens, keys or account
    identifiers are ever written.
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone

import serve_dashboard as sd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "public")

# Only these columns are copied. Anything else in the tabs is dropped.
ACT_FIELDS = [
    "activity_id", "date", "time", "name", "sport_type", "description",
    "distance_km", "distance_mi", "moving_time_min", "elapsed_time_min",
    "elevation_gain_m", "elevation_gain_ft",
    "avg_pace_min_per_km", "avg_pace_min_per_mi",
    "calories", "avg_cadence", "avg_heartrate", "max_heartrate",
    "suffer_score", "gear_brand", "gear_model",
]
LAP_FIELDS = [
    "activity_id", "date", "lap_index",
    "distance_km", "distance_mi", "moving_time_min", "elapsed_time_min",
    "elevation_gain_m", "elevation_gain_ft",
    "avg_pace_min_per_km", "avg_pace_min_per_mi",
    "avg_heartrate", "max_heartrate", "avg_cadence",
]

# Anything matching these in the output means a credential leaked into the
# sheet somehow. Cheap insurance -- publishing is irreversible.
FORBIDDEN = ("PRIVATE KEY", "client_secret", "refresh_token", "access_token",
             "BEGIN RSA", "hooks.slack.com")


def pick(row, fields):
    """Copy only `fields`, turning gspread's '' into None so the page's
    numeric parsing doesn't have to special-case blanks."""
    return {f: (None if row.get(f) == "" else row.get(f)) for f in fields}


def main():
    print("Building static snapshot from the Google Sheet…")

    sheet = sd.fetch_sheet()
    activities = [pick(r, ACT_FIELDS) for r in sheet["activities"]]
    laps = [pick(r, LAP_FIELDS) for r in sheet["laps"]]
    if not activities:
        sys.exit("ABORT: the STRAVA tab came back empty -- refusing to publish.")
    print(f"  {len(activities)} activities, {len(laps)} laps")

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raceDate": sd.RACE_DATE,
        "firstWeek": sd.FIRST_WEEK,
        "demo": sd.DEMO,
        "activities": activities,
        "laps": laps,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    data_path = os.path.join(OUT_DIR, "data.json")
    with open(data_path, "w") as f:
        json.dump(snapshot, f, separators=(",", ":"))
    shutil.copyfile(os.path.join(HERE, "dashboard.html"),
                    os.path.join(OUT_DIR, "index.html"))

    blob = open(data_path).read()
    for needle in FORBIDDEN:
        if needle in blob:
            os.remove(data_path)
            sys.exit(f"ABORT: '{needle}' found in data.json -- refusing to publish.")

    kb = os.path.getsize(data_path) / 1024
    print(f"  wrote public/data.json ({kb:.0f} KB) and public/index.html")


if __name__ == "__main__":
    main()
