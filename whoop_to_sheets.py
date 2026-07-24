#!/usr/bin/env python3
"""
WHOOP -> Google Sheets daily sync (standalone, for GitHub Actions or local cron).

Writes one row per day (physiological cycle) into a "WHOOP" tab in the SAME
Google Sheet used by strava_to_sheets.py, combining strain (cycle), recovery,
and sleep data. Individual workouts are not included (see README.md for why).

Requires these environment variables:
    WHOOP_CLIENT_ID
    WHOOP_CLIENT_SECRET
    WHOOP_REFRESH_TOKEN        (seed value, from get_whoop_tokens.py -- only
                                 used on the very first run; see note below)
    GOOGLE_SERVICE_ACCOUNT_JSON  (same one used for the Strava sync)
    GOOGLE_SHEET_ID            (same sheet ID used for the Strava sync -- this
                                 script does NOT create a sheet, it must already
                                 exist and already be shared with the service
                                 account)
    LOOKBACK_DAYS              (optional, default 3)
    SLACK_WEBHOOK_URL          (optional -- same webhook as the Strava sync)

IMPORTANT -- rotating refresh tokens:
WHOOP invalidates your refresh token every time you use it and issues a new
one. A GitHub Actions secret can't be updated by the workflow itself without
extra plumbing (a GitHub PAT + calling GitHub's API), so instead this script
stores the current refresh token in a hidden "_whoop_token_state" tab in the
same Google Sheet, which it already has write access to. On each run it reads
the stored token if present (falling back to the WHOOP_REFRESH_TOKEN secret
only on the very first run), and immediately writes back whatever new token
WHOOP issues -- before doing anything else -- so a later failure can't strand
you with an invalidated token.

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

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer"

WORKSHEET_TITLE = "WHOOP"
STATE_WORKSHEET_TITLE = "_whoop_token_state"
STATE_KEY = "whoop_refresh_token"

HEADER = [
    "cycle_id", "date",
    "strain", "avg_heart_rate", "max_heart_rate", "calories",
    "recovery_score", "resting_heart_rate", "hrv_ms", "spo2_percentage",
    "skin_temp_c", "skin_temp_f",
    "sleep_performance_pct", "sleep_efficiency_pct", "sleep_consistency_pct",
    "time_in_bed_hours", "light_sleep_hours", "rem_sleep_hours", "deep_sleep_hours",
    "awake_hours", "sleep_cycle_count", "disturbance_count", "respiratory_rate",
]


def get_sheets_client():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_worksheet(sh, title, header):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(header), 2))
    if not ws.row_values(1):
        ws.append_row(header)
    return ws


def get_stored_refresh_token(state_ws):
    for row in state_ws.get_all_values()[1:]:
        if row and row[0] == STATE_KEY:
            return row[1] if len(row) > 1 else None
    return None


def save_refresh_token(state_ws, token):
    values = state_ws.get_all_values()
    for i, row in enumerate(values[1:], start=2):
        if row and row[0] == STATE_KEY:
            state_ws.update_cell(i, 2, token)
            return
    state_ws.append_row([STATE_KEY, token])


def get_access_token(state_ws):
    refresh_token = get_stored_refresh_token(state_ws) or os.environ["WHOOP_REFRESH_TOKEN"]

    resp = requests.post(
        WHOOP_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": os.environ["WHOOP_CLIENT_ID"],
            "client_secret": os.environ["WHOOP_CLIENT_SECRET"],
            "scope": "offline",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Save the new refresh token immediately -- before any data fetching --
    # since the old one is now invalid and we don't want to strand ourselves
    # if something fails later in the run.
    save_refresh_token(state_ws, data["refresh_token"])

    return data["access_token"]


def whoop_get(path, token, params=None):
    resp = requests.get(
        f"{WHOOP_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def whoop_get_optional(path, token):
    try:
        return whoop_get(path, token)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def fetch_recent_cycles(token, lookback_days):
    start_iso = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    cycles, next_token = [], None
    while True:
        params = {"start": start_iso, "limit": 25}
        if next_token:
            params["nextToken"] = next_token
        data = whoop_get("/v2/cycle", token, params)
        cycles.extend(data.get("records", []))
        next_token = data.get("next_token")
        if not next_token:
            break
    return cycles


def local_date(start_iso: str, tz_offset: str) -> str:
    """start_iso like '2026-07-21T02:25:44.774Z' (UTC), tz_offset like '-05:00'."""
    cleaned = start_iso.rstrip("Z")
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in cleaned else "%Y-%m-%dT%H:%M:%S"
    dt_utc = datetime.strptime(cleaned, fmt)

    sign = -1 if tz_offset.startswith("-") else 1
    hh, mm = tz_offset.lstrip("+-").split(":")
    offset = sign * timedelta(hours=int(hh), minutes=int(mm))

    return (dt_utc + offset).date().isoformat()


def ms_to_hours(ms):
    return round(ms / 3_600_000, 2) if ms is not None else None


def build_row(cycle: dict, recovery: dict | None, sleep: dict | None) -> list:
    date_part = local_date(cycle.get("created_at", ""), cycle.get("timezone_offset", "+00:00"))

    cscore = cycle.get("score") or {}
    kilojoule = cscore.get("kilojoule")
    calories = round(kilojoule * 0.239006, 1) if kilojoule is not None else None

    rscore = (recovery or {}).get("score") or {}
    skin_c = rscore.get("skin_temp_celsius")
    skin_f = round(skin_c * 9 / 5 + 32, 1) if skin_c is not None else None

    sscore = (sleep or {}).get("score") or {}
    stage = sscore.get("stage_summary") or {}

    return [
        cycle.get("id"),
        date_part,
        cscore.get("strain"),
        cscore.get("average_heart_rate"),
        cscore.get("max_heart_rate"),
        calories,
        rscore.get("recovery_score"),
        rscore.get("resting_heart_rate"),
        rscore.get("hrv_rmssd_milli"),
        rscore.get("spo2_percentage"),
        skin_c,
        skin_f,
        sscore.get("sleep_performance_percentage"),
        sscore.get("sleep_efficiency_percentage"),
        sscore.get("sleep_consistency_percentage"),
        ms_to_hours(stage.get("total_in_bed_time_milli")),
        ms_to_hours(stage.get("total_light_sleep_time_milli")),
        ms_to_hours(stage.get("total_rem_sleep_time_milli")),
        ms_to_hours(stage.get("total_slow_wave_sleep_time_milli")),
        ms_to_hours(stage.get("total_awake_time_milli")),
        stage.get("sleep_cycle_count"),
        stage.get("disturbance_count"),
        sscore.get("respiratory_rate"),
    ]


def notify_slack(rows: list, sheet_url: str):
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url or not rows:
        return

    lines = []
    for row in rows:
        d = dict(zip(HEADER, row))
        bits = []
        if d.get("strain") is not None:
            bits.append(f"strain {d['strain']:.1f}" if isinstance(d["strain"], float) else f"strain {d['strain']}")
        if d.get("recovery_score") is not None:
            bits.append(f"recovery {d['recovery_score']}%")
        if d.get("sleep_performance_pct") is not None:
            bits.append(f"sleep {d['sleep_performance_pct']}%")
        lines.append(f"• *{d.get('date')}* ({', '.join(bits)})")

    text = (
        f":zzz: *WHOOP sync*: {len(rows)} new day(s) added to "
        f"<{sheet_url}|WHOOP tab>\n" + "\n".join(lines)
    )

    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Slack notification failed: {e}")


def main():
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "5"))

    gc = get_sheets_client()
    sh = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])

    state_ws = get_or_create_worksheet(sh, STATE_WORKSHEET_TITLE, ["key", "value"])
    token = get_access_token(state_ws)

    ws = get_or_create_worksheet(sh, WORKSHEET_TITLE, HEADER)
    existing_ids = set(ws.col_values(1)[1:])  # skip header

    cycles = fetch_recent_cycles(token, lookback_days)

    rows = []
    for cycle in cycles:
        if cycle.get("end") is None:
            continue  # today's in-progress cycle, or one WHOOP hasn't scored yet
        cid = str(cycle["id"])
        if cid in existing_ids:
            continue
        recovery = whoop_get_optional(f"/v2/cycle/{cid}/recovery", token)
        sleep = whoop_get_optional(f"/v2/cycle/{cid}/sleep", token)
        rows.append(build_row(cycle, recovery, sleep))
        time.sleep(0.3)  # be polite to WHOOP's rate limits

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"Synced {len(rows)} new day(s) to {sh.url} (WHOOP tab)")
        notify_slack(rows, sh.url)
    else:
        print("No new WHOOP days to sync.")


if __name__ == "__main__":
    main()
