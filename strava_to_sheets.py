#!/usr/bin/env python3
"""
Strava -> Google Sheets daily sync (standalone, for GitHub Actions or local cron).

Requires these environment variables:
    STRAVA_CLIENT_ID
    STRAVA_CLIENT_SECRET
    STRAVA_REFRESH_TOKEN       (from get_strava_tokens.py, one-time setup)
    GOOGLE_SERVICE_ACCOUNT_JSON  (the full JSON key contents, as a string)
    GOOGLE_SHEET_ID            (optional on first run -- if unset, a new sheet
                                 is created and its ID printed; save it as a
                                 secret for subsequent runs)
    SHEET_OWNER_EMAIL          (only used when creating a new sheet, to share
                                 it with you -- defaults to qrui0726@gmail.com)
    LOOKBACK_DAYS              (optional, default 3 -- how far back to check
                                 for activities; dedup against the sheet means
                                 overlap is harmless)

Install deps: pip install requests gspread google-auth
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials

STRAVA_API = "https://www.strava.com/api/v3"
SHEET_TITLE = "Strava Activity Log"
WORKSHEET_TITLE = "Activities"

HEADER = [
    "activity_id", "date", "name", "sport_type", "description",
    "distance_km", "distance_mi", "moving_time_min", "elapsed_time_min",
    "elevation_gain_m", "avg_speed_kmh", "max_speed_kmh", "avg_pace_min_per_km",
    "calories", "avg_cadence", "kudos_count", "achievement_count", "pr_count",
    "has_heartrate", "avg_heartrate", "max_heartrate",
    "has_device_watts", "avg_watts", "suffer_score",
    "gear_brand", "gear_model", "gear_name",
    "raw_json",
]


def get_access_token():
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": os.environ["STRAVA_CLIENT_ID"],
            "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
            "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def strava_get(path, token, params=None):
    resp = requests.get(
        f"{STRAVA_API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_recent_activities(token, lookback_days):
    after_epoch = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    activities, page = [], 1
    while True:
        batch = strava_get("/athlete/activities", token, {"after": after_epoch, "per_page": 100, "page": page})
        if not batch:
            break
        activities.extend(batch)
        page += 1
        if len(batch) < 100:
            break
    return activities


_gear_cache = {}

def fetch_gear(gear_id, token):
    if not gear_id:
        return None
    if gear_id not in _gear_cache:
        try:
            _gear_cache[gear_id] = strava_get(f"/gear/{gear_id}", token)
        except requests.HTTPError:
            _gear_cache[gear_id] = None
    return _gear_cache[gear_id]


def build_row(detail: dict, gear: dict | None) -> list:
    distance_m = detail.get("distance", 0) or 0
    avg_speed = detail.get("average_speed", 0) or 0

    distance_km = round(distance_m / 1000, 3)
    distance_mi = round(distance_m / 1609.34, 3)
    avg_pace = round((1000 / avg_speed) / 60, 2) if avg_speed else None

    return [
        detail.get("id"),
        detail.get("start_date_local"),
        detail.get("name"),
        detail.get("sport_type") or detail.get("type"),
        detail.get("description", "") or "",
        distance_km,
        distance_mi,
        round((detail.get("moving_time", 0) or 0) / 60, 2),
        round((detail.get("elapsed_time", 0) or 0) / 60, 2),
        detail.get("total_elevation_gain"),
        round(avg_speed * 3.6, 2) if avg_speed else None,
        round((detail.get("max_speed", 0) or 0) * 3.6, 2),
        avg_pace,
        detail.get("calories"),
        detail.get("average_cadence"),
        detail.get("kudos_count"),
        detail.get("achievement_count"),
        detail.get("pr_count"),
        detail.get("has_heartrate"),
        detail.get("average_heartrate"),
        detail.get("max_heartrate"),
        bool(detail.get("device_watts")),
        detail.get("average_watts"),
        detail.get("suffer_score"),
        (gear or {}).get("brand_name"),
        (gear or {}).get("model_name"),
        (gear or {}).get("name"),
        json.dumps({"activity": detail, "gear": gear}, separators=(",", ":"), default=str),
    ]


def get_sheets_client():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_sheet(gc):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if sheet_id:
        return gc.open_by_key(sheet_id), False

    owner_email = os.environ.get("SHEET_OWNER_EMAIL", "qrui0726@gmail.com")
    sh = gc.create(SHEET_TITLE)
    sh.share(owner_email, perm_type="user", role="writer")
    ws = sh.sheet1
    ws.update_title(WORKSHEET_TITLE)
    ws.append_row(HEADER)
    return sh, True


def main():
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "3"))

    token = get_access_token()
    activities = fetch_recent_activities(token, lookback_days)

    gc = get_sheets_client()
    sh, created = get_or_create_sheet(gc)
    ws = sh.worksheet(WORKSHEET_TITLE)

    if created:
        print(f"Created new sheet: {sh.url}")
        print(f"IMPORTANT: save this as the GOOGLE_SHEET_ID secret for future runs: {sh.id}")

    existing_ids = set(ws.col_values(1)[1:])  # skip header

    rows = []
    for a in activities:
        aid = str(a["id"])
        if aid in existing_ids:
            continue
        detail = strava_get(f"/activities/{aid}", token)
        gear = fetch_gear(detail.get("gear_id"), token)
        rows.append(build_row(detail, gear))
        time.sleep(0.3)  # be polite to Strava's rate limits

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"Synced {len(rows)} new activity(ies) to {sh.url}")
    else:
        print("No new activities to sync.")


if __name__ == "__main__":
    main()
