# Strava + WHOOP → Google Sheets Daily Sync

Pulls your Strava activities and WHOOP daily stats and appends them as rows
in a shared Google Sheet — Strava into an "Activities" tab, WHOOP into a
"WHOOP" tab.

## How it runs

This runs on **GitHub Actions**, not locally or in Cowork — Cowork's sandbox
blocks outbound calls to Google's, Strava's, and WHOOP's APIs, so a Cowork
scheduled task can't do this. GitHub Actions has normal internet access and
a free daily-cron tier.

- `strava_to_sheets.py` / `whoop_to_sheets.py` — the two sync scripts
- `get_strava_tokens.py` / `get_whoop_tokens.py` — one-time local helpers to
  get each service's refresh token
- `.github/workflows/strava_sync.yml` — one workflow, two independent jobs
  (`strava-sync`, `whoop-sync`), same daily schedule (fires at 10pm and 8am
  New York time year-round; see the comments in that file for how it handles
  the EST/EDT switch)

Both scripts share the same Google Sheet (`GOOGLE_SHEET_ID`), the same
service account (`GOOGLE_SERVICE_ACCOUNT_JSON`), and the same optional Slack
webhook (`SLACK_WEBHOOK_URL`) — you only set those up once, in the Strava
setup below.

---

## Part 1: Strava setup (~15 minutes)

### 1. Create a Strava API application
- Go to https://www.strava.com/settings/api
- Fill in any app name/website, set **Authorization Callback Domain** to `localhost`
- Note the **Client ID** and **Client Secret** shown

### 2. Get a Strava refresh token
Run locally on your machine (not in Cowork — this needs a browser):
```
pip install requests
python get_strava_tokens.py <client_id> <client_secret>
```
- It opens a Strava authorization page in your browser — click Authorize
- You'll land on a "can't connect to localhost" error page — that's expected.
  Copy the `code=...` value from that page's URL and paste it back into the terminal
- It prints `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN` — save these

### 3. Create a Google Cloud service account
- Google Cloud Console → enable the Sheets API and Drive API for a project
- IAM & Admin → Service Accounts → create one → Keys → Add Key → JSON
- This downloads a `service_account.json` file — keep it private, never commit it to git

### 4. Create the Google Sheet yourself and share it with the service account
Service accounts get **0 bytes of their own Drive storage**, so letting the
service account create the sheet itself fails on personal Gmail accounts with
`403: The user's Drive storage quota has been exceeded`. Instead:

1. Go to https://sheets.new to create a blank sheet in your own Google account
2. Click **Share**, and share it with your service account's email address —
   it's the `client_email` field in `service_account.json` (looks like
   `strava-sync@your-project.iam.gserviceaccount.com`) — give it **Editor** access
3. Copy the sheet's ID from its URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

This same sheet and share is reused for WHOOP — no separate sharing step needed in Part 2.

### 5. (Optional) Create a Slack Incoming Webhook for notifications
Used by both syncs. Only needed if you want a Slack message posted whenever
new data is synced (skipped on no-op runs, so no spam from the multiple
daily checks).

1. Go to https://api.slack.com/apps → "Create New App" → "From scratch"
2. Name it anything, pick your workspace
3. Left sidebar → "Incoming Webhooks" → toggle it on → "Add New Webhook to Workspace"
4. Pick the channel you want notifications in, authorize it
5. Copy the webhook URL it gives you (looks like `https://hooks.slack.com/services/...`)

Skip this whole step if you don't want Slack notifications — neither script
sends anything if `SLACK_WEBHOOK_URL` isn't set.

### 6. Create a GitHub repo and add secrets
- Create a new **private** GitHub repo
- Push everything in this folder to it (the workflow file must stay at the
  repo root under `.github/workflows/`)
- Settings → Secrets and variables → Actions → New repository secret. Add:
  - `STRAVA_CLIENT_ID`
  - `STRAVA_CLIENT_SECRET`
  - `STRAVA_REFRESH_TOKEN`
  - `GOOGLE_SERVICE_ACCOUNT_JSON` — the entire contents of `service_account.json`, as one blob
  - `GOOGLE_SHEET_ID` — the sheet ID from step 4
  - `SLACK_WEBHOOK_URL` — (optional) the webhook URL from step 5

### 7. Bootstrap and verify
Run once locally to confirm everything's wired up correctly:
```
pip install requests gspread google-auth
export STRAVA_CLIENT_ID=...
export STRAVA_CLIENT_SECRET=...
export STRAVA_REFRESH_TOKEN=...
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service_account.json)"
export GOOGLE_SHEET_ID=<the ID from step 4>
python strava_to_sheets.py
```
This writes the header row and does the first sync.

---

## Part 2: WHOOP setup (~10 minutes, do this after Part 1)

### 1. Create a WHOOP developer app
- Go to https://developer-dashboard.whoop.com and create an app
- Set the **Redirect URI** to exactly `http://localhost/callback` (WHOOP
  requires an exact match here, unlike Strava's domain-only setting — if you
  use a different value, pass it as a third argument to the script below)
- Note the **Client ID** and **Client Secret** shown

### 2. Get a WHOOP refresh token
```
pip install requests
python get_whoop_tokens.py <client_id> <client_secret>
```
Same flow as the Strava helper — open the URL, authorize, paste back the
`code` from the redirect. Prints `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET`,
`WHOOP_REFRESH_TOKEN` — save these.

**Note on WHOOP's rotating refresh tokens:** unlike Strava, WHOOP invalidates
your refresh token every time it's used and issues a new one. Since a GitHub
Actions secret can't be updated by the workflow itself without extra
plumbing, `whoop_to_sheets.py` instead stores the current refresh token in a
hidden `_whoop_token_state` tab in your Google Sheet, and updates it on every
run. `WHOOP_REFRESH_TOKEN` is only ever used to seed the very first run —
don't delete that state tab, and don't manually edit it.

### 3. Add the WHOOP secrets to the same GitHub repo
- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- `WHOOP_REFRESH_TOKEN`

(`GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID`, and `SLACK_WEBHOOK_URL` are already set from Part 1 and reused as-is.)

### 4. Bootstrap and verify
```
export WHOOP_CLIENT_ID=...
export WHOOP_CLIENT_SECRET=...
export WHOOP_REFRESH_TOKEN=...
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service_account.json)"
export GOOGLE_SHEET_ID=<same sheet ID as Part 1>
python whoop_to_sheets.py
```
This creates the "WHOOP" tab and the `_whoop_token_state` tab, writes headers,
and does the first sync.

Then in the GitHub repo → Actions tab → "Strava to Google Sheets Sync" →
"Run workflow" to confirm both jobs run clean end to end. After that, both
run automatically on the same daily schedule.

---

## How it works day to day

**Strava** (`strava_to_sheets.py`):
- Mints a fresh access token from the refresh token (Strava's refresh tokens don't expire)
- Pulls activities from the last 3 days (`LOOKBACK_DAYS`), dedupes against the
  existing `activity_id` column, fetches full detail + gear for new ones
- Posts a Slack summary if new activities were synced

**WHOOP** (`whoop_to_sheets.py`):
- Reads the current refresh token from the `_whoop_token_state` tab (or the
  `WHOOP_REFRESH_TOKEN` secret on the very first run), refreshes it, and
  immediately writes the new rotated token back before doing anything else
- Pulls physiological cycles (days) from the last 3 days (`LOOKBACK_DAYS`),
  skipping any not yet fully scored (e.g. today's in-progress cycle)
- Dedupes against the existing `cycle_id` column; for new days, fetches that
  day's recovery and sleep data and combines all three into one row
- Posts a Slack summary if new days were synced

Both scripts are safe to double-fire (the multiple daily cron triggers that
handle EST/EDT are harmless no-ops when there's nothing new).

## Sheet columns

**Activities tab (Strava):**
`activity_id, date, time, name, sport_type, description, distance_km,
distance_mi, moving_time_min, elapsed_time_min, elevation_gain_m,
elevation_gain_ft, avg_pace_min_per_km, avg_pace_min_per_mi, calories,
avg_cadence, has_heartrate, avg_heartrate, max_heartrate, avg_watts,
suffer_score, gear_brand, gear_model`

**WHOOP tab (one row per day):**
`cycle_id, date, strain, avg_heart_rate, max_heart_rate, calories,
recovery_score, resting_heart_rate, hrv_ms, spo2_percentage, skin_temp_c,
skin_temp_f, sleep_performance_pct, sleep_efficiency_pct,
sleep_consistency_pct, time_in_bed_hours, light_sleep_hours,
rem_sleep_hours, deep_sleep_hours, awake_hours, sleep_cycle_count,
disturbance_count, respiratory_rate`

Individual WHOOP workouts aren't included — only daily strain/recovery/sleep
summaries. If you want workout-level rows later (similar shape to the Strava
tab), that's a separate addition.

If you change either schema (add/remove/reorder columns) after a tab already
has data: clear that tab's contents (not the tab itself), then re-run — each
script only writes a header row when its tab is empty, so it needs a clean
slate to avoid misaligning old rows with new columns.

## Troubleshooting
- **`403: Drive storage quota exceeded`** — you're missing the "share the
  sheet with the service account" step; the service account can't create its
  own sheet, it needs to be shared one
- **`gspread.exceptions.APIError` with an HTML "Page Not Found" body** —
  `GOOGLE_SHEET_ID` is malformed (stray quotes/whitespace, or the full URL
  pasted instead of just the ID substring between `/d/` and `/edit`)
- **Columns look shifted/misaligned** — see "Sheet columns" above, clear the
  tab and re-run
- **WHOOP sync fails with an invalid/expired refresh token error** — check
  the `_whoop_token_state` tab still has a value; if it was accidentally
  cleared, re-run `get_whoop_tokens.py` and update the `WHOOP_REFRESH_TOKEN`
  secret to reseed it
