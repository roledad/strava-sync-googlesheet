#!/usr/bin/env python3
"""
interval.icu -> Google Sheets daily sync (standalone, for GitHub Actions or local cron).

Replaces strava_to_sheets.py. Strava now requires a paid Strava subscription
just to use its API (as of their June 2026 developer agreement update), so
this script reads the same activity data from interval.icu instead --
interval.icu's API is free and uses a simple per-athlete API key (no OAuth).

IMPORTANT CAVEAT: interval.icu will NOT return activity data for any
activity whose source is Strava -- their API responds with only
{id, start_date_local, source, "_note": "STRAVA activities are not
available via the API"}. This is interval.icu complying with Strava's
API terms, not a bug. In practice this means:
  - Activities recorded on a device/app that syncs to interval.icu
    *directly* (Coros, Garmin, Wahoo, Suunto, manual upload, etc. --
    configured at https://intervals.icu/settings under the "Connections"
    tab) come through with full data and work fine here.
  - Activities that only exist in interval.icu because it pulled them in
    from a connected Strava account come through as empty stubs. This
    script detects and skips those (see SKIPPED note in the log output)
    rather than writing a mostly-blank row -- they simply won't appear in
    the sheet until/unless you backfill them some other way.

Requires these environment variables:
    INTERVALS_API_KEY          your interval.icu API key, from
                                https://intervals.icu/settings (bottom of
                                page, under "Developer Settings")
    INTERVALS_ATHLETE_ID       (optional, default "0" -- "0" means "the
                                athlete who owns the API key", so you
                                normally never need to set this)
    GOOGLE_SERVICE_ACCOUNT_JSON  (the full JSON key contents, as a string)
    GOOGLE_SHEET_ID            (optional on first run -- if unset, a new sheet
                                 is created and its ID printed; save it as a
                                 secret for subsequent runs)
    SHEET_OWNER_EMAIL          (only used when creating a new sheet, to share
                                 it with you -- defaults to qrui0726@gmail.com)
    LOOKBACK_DAYS              (optional, default 7 -- how far back to check
                                 for activities; dedup against the sheet means
                                 overlap is harmless)
    SLACK_WEBHOOK_URL          (optional -- if set, posts a summary message to
                                 that Slack Incoming Webhook whenever new
                                 activities are synced; skipped on no-op runs)

For every newly-synced activity with type exactly "Run", also fetches
GET /activity/{id}/intervals and writes one row per interval to a separate
"Details" tab in the same sheet, keyed by activity_id and date -- this is
interval.icu's equivalent of Strava's per-lap breakdown. Whether these line
up with your watch's actual lap-button presses depends on the "interval
detection" method configured for the Run sport in interval.icu's settings
(set it to "Laps" there if you want a 1:1 match with device laps, similar
to how Strava reported them).

The sheet's tab names and column headers are unchanged from the Strava
version on purpose, so dashboard.html / build_static.py need no changes.

Install deps: pip install requests gspread google-auth
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials

INTERVALS_API = "https://intervals.icu/api/v1"
SHEET_TITLE = "NYCM Log"
WORKSHEET_TITLE = "STRAVA"
LAPS_WORKSHEET_TITLE = "Details"

HEADER = [
    "activity_id", "date", "time", "name", "sport_type", "description",
    "distance_km", "distance_mi", "moving_time_min", "elapsed_time_min",
    "elevation_gain_m", "elevation_gain_ft", "avg_pace_min_per_km", "avg_pace_min_per_mi",
    "calories", "avg_cadence",
    "has_heartrate", "avg_heartrate", "max_heartrate",
    "avg_watts", "suffer_score",
    "gear_brand", "gear_model",
]

LAPS_HEADER = [
    "activity_id", "date", "lap_index", "lap_name",
    "distance_km", "distance_mi", "moving_time_min", "elapsed_time_min",
    "elevation_gain_m", "elevation_gain_ft",
    "avg_pace_min_per_km", "avg_pace_min_per_mi",
    "avg_heartrate", "max_heartrate", "avg_cadence", "avg_watts",
]


def intervals_get(path, api_key, params=None):
    resp = requests.get(
        f"{INTERVALS_API}{path}",
        auth=("API_KEY", api_key),
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_recent_activities(athlete_id, api_key, lookback_days):
    """
    GET /athlete/{id}/activities returns full Activity objects directly (no
    separate per-id detail call needed, unlike Strava) -- except for
    Strava-sourced activities, which come back as bare stubs (see module
    docstring). oldest/newest are plain dates, no pagination needed.
    """
    now = datetime.now(timezone.utc)
    oldest = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    newest = now.strftime("%Y-%m-%d")
    return intervals_get(
        f"/athlete/{athlete_id}/activities", api_key,
        {"oldest": oldest, "newest": newest},
    )


def fetch_laps(activity_id, api_key):
    """GET /activity/{id}/intervals -- interval.icu's equivalent of Strava's
    per-lap breakdown. Returns IntervalsDTO {id, analyzed, icu_intervals,
    icu_groups}; we want icu_intervals (one entry per detected/recorded
    interval)."""
    try:
        data = intervals_get(f"/activity/{activity_id}/intervals", api_key)
        return data.get("icu_intervals") or []
    except requests.HTTPError:
        return []


def build_lap_rows(activity_id, date_part: str, intervals: list) -> list:
    rows = []
    for i, iv in enumerate(intervals, start=1):
        distance_m = iv.get("distance", 0) or 0
        avg_speed = iv.get("average_speed", 0) or 0

        distance_km = round(distance_m / 1000, 3)
        distance_mi = round(distance_m / 1609.34, 3)
        avg_pace_km = round((1000 / avg_speed) / 60, 2) if avg_speed else None
        avg_pace_mi = round(avg_pace_km * 1.60934, 2) if avg_pace_km is not None else None

        elevation_gain_m = iv.get("total_elevation_gain")
        elevation_gain_ft = round(elevation_gain_m * 3.28084, 1) if elevation_gain_m is not None else None

        rows.append([
            activity_id,
            date_part,
            i,
            iv.get("label") or f"Lap {i}",
            distance_km,
            distance_mi,
            round((iv.get("moving_time", 0) or 0) / 60, 2),
            round((iv.get("elapsed_time", 0) or 0) / 60, 2),
            elevation_gain_m,
            elevation_gain_ft,
            avg_pace_km,
            avg_pace_mi,
            iv.get("average_heartrate"),
            iv.get("max_heartrate"),
            iv.get("average_cadence"),
            iv.get("average_watts"),
        ])
    return rows


def build_row(a: dict) -> list:
    distance_m = a.get("distance", 0) or 0
    avg_speed = a.get("average_speed", 0) or 0

    distance_km = round(distance_m / 1000, 3)
    distance_mi = round(distance_m / 1609.34, 3)
    avg_pace_km = round((1000 / avg_speed) / 60, 2) if avg_speed else None
    avg_pace_mi = round(avg_pace_km * 1.60934, 2) if avg_pace_km is not None else None

    # total_elevation_gain is in meters, same convention as Strava's field
    # of the same name.
    elevation_gain_m = a.get("total_elevation_gain")
    elevation_gain_ft = round(elevation_gain_m * 3.28084, 1) if elevation_gain_m is not None else None

    # start_date_local looks like "2026-09-05T22:49:46" (no trailing Z on
    # interval.icu, unlike Strava, but the split logic works either way).
    start_local = a.get("start_date_local") or ""
    if "T" in start_local:
        date_part, time_part = start_local.split("T", 1)
        time_part = time_part.rstrip("Z")
    else:
        date_part, time_part = start_local, ""

    # interval.icu's gear object only has a single free-text "name" field
    # (no brand/model split like Strava's gear API) -- e.g. "Altra Escalante".
    # We keep the gear_brand column for sheet/dashboard compatibility but it
    # will always be blank going forward; the full name goes in gear_model.
    gear = a.get("gear") or {}

    return [
        a.get("id"),
        date_part,
        time_part,
        a.get("name"),
        a.get("type"),
        a.get("description", "") or "",
        distance_km,
        distance_mi,
        round((a.get("moving_time", 0) or 0) / 60, 2),
        round((a.get("elapsed_time", 0) or 0) / 60, 2),
        elevation_gain_m,
        elevation_gain_ft,
        avg_pace_km,
        avg_pace_mi,
        a.get("calories"),
        a.get("average_cadence"),
        a.get("has_heartrate"),
        a.get("average_heartrate"),
        a.get("max_heartrate"),
        a.get("icu_average_watts"),
        a.get("icu_training_load"),  # closest interval.icu analog to Strava's suffer score
        None,  # gear_brand -- not split out by interval.icu, see gear_model
        gear.get("name"),  # gear_model
    ]


def notify_slack(rows: list, sheet_url: str):
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url or not rows:
        return

    lines = []
    for row in rows:
        d = dict(zip(HEADER, row))
        bits = [d.get("sport_type") or "Activity"]
        if d.get("distance_mi"):
            bits.append(f"{d['distance_mi']} mi")
        if d.get("moving_time_min"):
            bits.append(f"{d['moving_time_min']} min")
        lines.append(f"• *{d.get('name') or 'Untitled'}* ({', '.join(bits)})")

    text = (
        f":runner: *interval.icu sync*: {len(rows)} new activity(ies) added to "
        f"<{sheet_url}|Strava Activity Log>\n" + "\n".join(lines)
    )

    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        # Don't fail the whole sync just because the Slack ping didn't land
        print(f"Slack notification failed: {e}")


def get_sheets_client():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_sheet(gc):
    """
    Returns (spreadsheet, worksheet). Note: service accounts have 0 bytes of
    their own Drive storage, so gc.create() fails with a quota error on
    personal Gmail accounts. GOOGLE_SHEET_ID should normally be set, pointing
    at a sheet you created yourself and shared with the service account's
    client_email as Editor -- see SETUP_INSTRUCTIONS.md step 5. The gc.create()
    fallback below only works on Google Workspace accounts with Drive quota
    granted to service accounts.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if sheet_id:
        sh = gc.open_by_key(sheet_id)
    else:
        owner_email = os.environ.get("SHEET_OWNER_EMAIL", "qrui0726@gmail.com")
        sh = gc.create(SHEET_TITLE)
        sh.share(owner_email, perm_type="user", role="writer")
        print(f"Created new sheet: {sh.url}")
        print(f"IMPORTANT: save this as the GOOGLE_SHEET_ID secret for future runs: {sh.id}")

    try:
        ws = sh.worksheet(WORKSHEET_TITLE)
    except gspread.WorksheetNotFound:
        ws = sh.sheet1
        ws.update_title(WORKSHEET_TITLE)

    if not ws.row_values(1):
        ws.append_row(HEADER)

    return sh, ws


def get_or_create_worksheet(sh, title, header):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(header), 2))
    if not ws.row_values(1):
        ws.append_row(header)
    return ws


def main():
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "7"))
    api_key = os.environ["INTERVALS_API_KEY"]
    athlete_id = os.environ.get("INTERVALS_ATHLETE_ID", "0")

    activities = fetch_recent_activities(athlete_id, api_key, lookback_days)

    gc = get_sheets_client()
    sh, ws = get_or_create_sheet(gc)

    existing_ids = set(ws.col_values(1)[1:])  # skip header

    rows = []
    lap_rows = []
    skipped_strava_sourced = 0

    for a in activities:
        aid = str(a.get("id"))
        if aid in existing_ids:
            continue

        # Strava-sourced activities come back as bare stubs -- see module
        # docstring. Skip rather than writing a mostly-blank row; they stay
        # eligible to be picked up later if you backfill them some other way.
        if a.get("source") == "STRAVA" or "_note" in a:
            skipped_strava_sourced += 1
            continue

        row = build_row(a)
        rows.append(row)

        if a.get("type") == "Run":
            date_part = row[1]  # already computed by build_row, keep it in sync
            laps = fetch_laps(aid, api_key)
            if laps:
                lap_rows.extend(build_lap_rows(aid, date_part, laps))

        time.sleep(0.2)  # be polite to interval.icu's API

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"Synced {len(rows)} new activity(ies) to {sh.url}")
        notify_slack(rows, sh.url)
    else:
        print("No new activities to sync.")

    if lap_rows:
        laps_ws = get_or_create_worksheet(sh, LAPS_WORKSHEET_TITLE, LAPS_HEADER)
        laps_ws.append_rows(lap_rows, value_input_option="USER_ENTERED")
        print(f"Synced {len(lap_rows)} lap row(s) to {sh.url} ({LAPS_WORKSHEET_TITLE} tab)")

    if skipped_strava_sourced:
        print(
            f"Skipped {skipped_strava_sourced} Strava-sourced activity(ies) in the "
            "lookback window -- interval.icu doesn't expose their data via the API. "
            "See the module docstring for how to fix this at the source (connect "
            "your recording device directly to interval.icu instead of via Strava)."
        )


if __name__ == "__main__":
    main()
