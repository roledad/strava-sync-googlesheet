# Strava → Google Sheets Daily Sync

Pulls your Strava activities daily and appends them as rows in a Google Sheet.

## How it runs

This runs on **GitHub Actions**, not locally or in Cowork — Cowork's sandbox
blocks outbound calls to Google's and Strava's APIs, so a Cowork scheduled
task can't do this. GitHub Actions has normal internet access and a free
daily-cron tier.

- `strava_to_sheets.py` — the sync script
- `get_strava_tokens.py` — one-time local helper to get a Strava refresh token
- `.github/workflows/strava_sync.yml` — the daily schedule (fires at 10pm
  New York time year-round; see the comments in that file for how it handles
  the EST/EDT switch)

## One-time setup (~15 minutes)

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

### 5. (Optional) Create a Slack Incoming Webhook for notifications
Only needed if you want a Slack message posted whenever new activities are
synced (skipped on no-op runs, so no spam from the multiple daily checks).

1. Go to https://api.slack.com/apps → "Create New App" → "From scratch"
2. Name it anything, pick your workspace
3. Left sidebar → "Incoming Webhooks" → toggle it on → "Add New Webhook to Workspace"
4. Pick the channel you want notifications in, authorize it
5. Copy the webhook URL it gives you (looks like `https://hooks.slack.com/services/...`)

Skip this whole step if you don't want Slack notifications — the script just
won't send anything if `SLACK_WEBHOOK_URL` isn't set.

### 6. Create a GitHub repo and add secrets
- Create a new **private** GitHub repo
- Push `strava_to_sheets.py` and `.github/workflows/strava_sync.yml` to it
  (the workflow file must stay at the repo root under `.github/workflows/`)
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
This writes the header row and does the first sync. Then in the GitHub repo →
Actions tab → "Strava to Google Sheets Sync" → "Run workflow" to confirm the
CI path also runs clean end to end. After that it runs automatically every
day.

## How it works day to day
- Each run mints a fresh Strava access token from the refresh token (refresh
  tokens don't expire unless revoked)
- Pulls activities from the last 3 days (configurable via `LOOKBACK_DAYS` env
  var) — the overlap is intentional safety margin
- Reads the existing `activity_id` column from the sheet to skip anything
  already synced, so re-running or double-firing is harmless
- For genuinely new activities, fetches full detail + gear info and appends
  one row per activity
- If `SLACK_WEBHOOK_URL` is set and at least one new activity was synced,
  posts a summary message to that Slack channel (name, sport type, distance,
  time, and a link to the sheet). Silent on no-op runs, and a failed Slack
  post won't fail the sync itself.

## Sheet columns
`activity_id, date, time, name, sport_type, description, distance_km,
distance_mi, moving_time_min, elapsed_time_min, elevation_gain_m,
elevation_gain_ft, avg_pace_min_per_km, avg_pace_min_per_mi, calories,
avg_cadence, has_heartrate, avg_heartrate, max_heartrate, avg_watts,
suffer_score, gear_brand, gear_model`

If you change the schema (add/remove/reorder columns) after the sheet
already has data: clear the "Activities" tab's contents (not the tab itself),
then re-run — the script only writes a header row when the sheet is empty,
so it needs a clean slate to avoid misaligning old rows with new columns.

## Troubleshooting
- **`403: Drive storage quota exceeded`** — you're missing step 4; the
  service account can't create its own sheet, it needs to be shared one
- **`gspread.exceptions.APIError` with an HTML "Page Not Found" body** —
  `GOOGLE_SHEET_ID` is malformed (stray quotes/whitespace, or the full URL
  pasted instead of just the ID substring between `/d/` and `/edit`)
- **Columns look shifted/misaligned** — see "Sheet columns" above, clear the
  tab and re-run
