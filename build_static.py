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
from datetime import date, datetime, timezone

import serve_dashboard as sd

HERE = os.path.dirname(os.path.abspath(__file__))

# A --demo build writes to public_demo/ so it can never clobber the real
# snapshot in public/, which is the thing Cloudflare Pages actually deploys.
OUT_DIR = os.path.join(HERE, "public_demo" if sd.DEMO else "public")

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

# --------------------------------------------------------------------------
# Past training cycles
#
# past_activities.json is a one-off dump of the full Strava history (2019-2025,
# activity summaries only -- no laps). It lives at the repo root rather than in
# public/ because it's ~6.7 MB of build input; only the trimmed, per-cycle
# extract below is ever served.
#
# Boundaries are explicit rather than inferred. The goal race is taken to be
# the longest run on the end date, and its elapsed time is the finish time.
# --------------------------------------------------------------------------
PAST_SOURCE = os.path.join(HERE, "past_activities.json")
CYCLES = [
    ("2025 Indy Marathon",              "2025-07-21", "2025-11-08"),
    ("2025 NYC/Brooklyn Half Marathon", "2025-02-02", "2025-05-17"),
    ("2024 Brooklyn Half Marathon",     "2024-01-01", "2024-05-18"),
    ("2022 Twin-Cities Marathon",       "2022-05-16", "2022-10-02"),
    ("2021 CIM Marathon",               "2021-06-21", "2021-12-05"),
    ("2020 NYC Virtual Marathon",       "2020-08-09", "2020-10-17"),
    ("2019 Twin-Cities Marathon",       "2019-07-29", "2019-10-06"),
]

M_PER_MI = 1609.34
FT_PER_M = 3.28084


def past_row(a):
    """Reshape a raw Strava activity into the same columns the STRAVA sheet
    tab uses, so the dashboard's existing parsing works on it untouched."""
    local = a.get("start_date_local") or ""
    date_part, _, time_part = local.partition("T")
    dist = a.get("distance") or 0
    speed = a.get("average_speed") or 0
    elev_m = a.get("total_elevation_gain")
    pace_km = round((1000 / speed) / 60, 2) if speed else None
    return {
        "activity_id": a.get("id"),
        "date": date_part,
        "time": time_part[:8],
        "name": a.get("name") or "Untitled",
        "sport_type": a.get("sport_type") or a.get("type"),
        "distance_km": round(dist / 1000, 3),
        "distance_mi": round(dist / M_PER_MI, 3),
        "moving_time_min": round((a.get("moving_time") or 0) / 60, 2),
        "elapsed_time_min": round((a.get("elapsed_time") or 0) / 60, 2),
        "elevation_gain_m": elev_m,
        "elevation_gain_ft": round(elev_m * FT_PER_M, 1) if elev_m is not None else None,
        "avg_pace_min_per_km": pace_km,
        "avg_pace_min_per_mi": round(pace_km * 1.60934, 2) if pace_km is not None else None,
        "avg_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "avg_cadence": a.get("average_cadence"),
    }


def build_past_cycles():
    """One entry per training cycle: the runs inside it, plus the goal race."""
    if not os.path.exists(PAST_SOURCE):
        print(f"  (no {os.path.basename(PAST_SOURCE)}; skipping past cycles)")
        return []

    with open(PAST_SOURCE) as f:
        raw = json.load(f)
    runs = [a for a in raw
            if (a.get("sport_type") or a.get("type")) == "Run" and a.get("start_date_local")]

    out = []
    for name, start, end in CYCLES:
        rows = [past_row(a) for a in runs if start <= a["start_date_local"][:10] <= end]
        rows.sort(key=lambda r: (r["date"], r["time"]))
        if not rows:
            print(f"  ! {name}: no runs in range, skipped")
            continue

        # The goal race is the longest run on race day.
        on_day = [r for r in rows if r["date"] == end]
        race = max(on_day, key=lambda r: r["distance_km"]) if on_day else None
        weeks = -(-(date.fromisoformat(end) - date.fromisoformat(start)).days // 7)

        out.append({
            "name": name, "start": start, "raceDate": end,
            "firstWeek": max(1, weeks), "activities": rows,
            "finish": None if not race else {
                "distance_mi": race["distance_mi"], "distance_km": race["distance_km"],
                "elapsed_min": race["elapsed_time_min"], "moving_min": race["moving_time_min"],
                "name": race["name"],
            },
        })
        fin = out[-1]["finish"]
        print(f"  {name}: {len(rows)} runs, {weeks} weeks" +
              (f", finish {fin['distance_mi']:.2f} mi in {fin['elapsed_min']:.1f} min" if fin
               else ", NO RACE FOUND on end date"))
    return out

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

    cycles = build_past_cycles()

    os.makedirs(OUT_DIR, exist_ok=True)
    if cycles:
        past_path = os.path.join(OUT_DIR, "past_cycles.json")
        with open(past_path, "w") as f:
            json.dump({"cycles": cycles}, f, separators=(",", ":"))
        print(f"  wrote {os.path.basename(OUT_DIR)}/past_cycles.json "
              f"({os.path.getsize(past_path)/1024:.0f} KB)")

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

    out = os.path.basename(OUT_DIR)
    kb = os.path.getsize(data_path) / 1024
    print(f"  wrote {out}/data.json ({kb:.0f} KB) and {out}/index.html")


if __name__ == "__main__":
    main()
