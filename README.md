# Strava + WHOOP → Google Sheets Daily Sync

Pulls your Strava activities and WHOOP daily stats and appends them as rows
in a shared Google Sheet — Strava into a "STRAVA" tab, per-lap detail for Run
activities into a "Details" tab, and WHOOP into a "WHOOP" tab.

## How it runs

This runs on **GitHub Actions**, not locally or in Cowork — Cowork's sandbox
blocks outbound calls to Google's, Strava's, and WHOOP's APIs, so a Cowork
scheduled task can't do this. GitHub Actions has normal internet access and
a free daily-cron tier.

- `strava_to_sheets.py` / `whoop_to_sheets.py` — the two sync scripts
- `get_strava_tokens.py` / `get_whoop_tokens.py` — one-time local helpers to
  get each service's refresh token
- `dashboard.html` / `serve_dashboard.py` / `build_static.py` — the training
  dashboard, its local server, and the static-site builder (see "Training
  dashboard" and "Publishing the dashboard" below)
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
- For any newly-synced activity with `sport_type` exactly `"Run"`, also fetches
  `GET /activities/{id}/laps` and writes one row per lap to the "Running
  details" tab, keyed by `activity_id` and `date`. Laps only get fetched the
  first time an activity is synced (they ride along with the same
  new-activity dedup as the main tab), so there's no separate dedup logic
  needed here
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

**STRAVA tab (one row per activity):**
`activity_id, date, time, name, sport_type, description, distance_km,
distance_mi, moving_time_min, elapsed_time_min, elevation_gain_m,
elevation_gain_ft, avg_pace_min_per_km, avg_pace_min_per_mi, calories,
avg_cadence, has_heartrate, avg_heartrate, max_heartrate, avg_watts,
suffer_score, gear_brand, gear_model`

**Details tab (one row per lap, Run activities only):**
`activity_id, date, lap_index, lap_name, distance_km, distance_mi,
moving_time_min, elapsed_time_min, elevation_gain_m, elevation_gain_ft,
avg_pace_min_per_km, avg_pace_min_per_mi, avg_heartrate, max_heartrate,
avg_cadence, avg_watts`

`activity_id` + `date` let you join a lap back to its parent row on the
STRAVA tab. Non-Run activities (rides, swims, yoga, etc.) never get lap rows.

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

## Training dashboard (`dashboard.html` + `serve_dashboard.py`)

A local dashboard with three sections:

1. **Weekly summary** — six cards (weeks to race day, distance / moving time
   / elevation this week, 4-week average, peak week) over one row per week
   counting down to race day, with activity count, cumulative moving and
   elapsed time, distance and elevation gain, plus a Mon–Sun day grid shaded
   by mileage. Built from the `STRAVA` tab.
2. **Distributions** — six histograms, split by level:
   - *per activity*, from the `STRAVA` tab: distance, moving time, elevation gain
   - *per lap*, from the `Details` tab: average pace, average heart rate, average cadence

   All but heart rate use fixed buckets (see below); heart rate picks bin
   widths automatically via the Freedman–Diaconis rule. A dashed line marks
   the median. The scope selector offers **All** (meaning since the block
   started — with the default `FIRST_WEEK=15` that's 2026-07-19), last 30
   days, or last 10 days.
3. **Recent activities** — the last 10 days, as a 10-day window over the
   same `STRAVA` rows. The selector filters to runs or non-runs. Hover a row
   for a summary card; click it to expand full detail and the splits table
   (read from the `Details` tab, so expanding costs no network request).

Miles/kilometers toggle applies everywhere. Preferences persist per browser.

### Everything comes from the sheet

The dashboard never calls the Strava API. `strava_to_sheets.py` has already
written every field it needs into the two tabs, so re-fetching from Strava
would be a slower way to get identical numbers — and would spend rate limit
on every page load or build. One consequence worth knowing: the dashboard is
exactly as fresh as the last sync, so an activity uploaded an hour ago won't
appear until the next run (or until you run `strava_to_sheets.py` yourself).

Three fields exist only in the API and so aren't shown: `kudos_count`,
`achievement_count` and `max_speed`. If you ever want them, add the columns
to `HEADER` in `strava_to_sheets.py` and they'll flow through.

### Why there's a server

A plain HTML file opened from disk can't read the sheet — that needs the
service account. `serve_dashboard.py` holds that one credential, binds to
`127.0.0.1` only, and exposes a small read-only JSON API to the page. It
needs no Strava credentials at all.

### Setup

```
pip install gspread google-auth
```

Create a `.env` next to the scripts (already in `.gitignore`):

```
GOOGLE_SHEET_ID=...
RACE_DATE=2026-11-01
FIRST_WEEK=15
```

Google credentials fall back to the `service_account.json` already in this
folder, so `GOOGLE_SERVICE_ACCOUNT_JSON` is only needed if you keep the key
elsewhere. Plain environment variables work too, if you'd rather not have a
`.env`. No `STRAVA_*` values are needed here — those belong to the sync
scripts, not the dashboard.

Then:

```
python serve_dashboard.py
```

It opens <http://127.0.0.1:8420/>. `--port N` changes the port, `--no-open`
skips the browser, and `--demo` runs the whole thing on generated data with
no credentials at all — useful for checking the page renders before wiring
anything up. `build_static.py --demo` writes to `public_demo/` (gitignored)
rather than `public/`, so a test build can't overwrite the real snapshot
that Cloudflare deploys.

`RACE_DATE` and `FIRST_WEEK` are validated at import: a malformed date or an
out-of-range week number exits with a message instead of producing a
snapshot the page renders as `NaN`. Empty values are treated as unset, since
GitHub Actions expands an undefined `${{ vars.X }}` to an empty string rather
than omitting it.

### How the week math works

Weeks count **down** to `RACE_DATE`, so week 0 is race day and week N starts
`7N` days before it. Matching the reference layout, the two halves of the
table use different week boundaries on purpose:

- the cumulative columns cover **Sunday → Saturday** (`start` … `start+6`),
  which is the range shown in the Start/End Date columns
- the day grid covers **Monday → Sunday** (`start+1` … `start+7`)

So a week's Sunday mileage appears in the *previous* row's grid, and the grid
row will not sum to that row's Cum Distance. (Verified against the reference
sheet: week 14 = Sun 7/26's 15.1 + Mon–Sat's 33.4 = 48.50.)

`FIRST_WEEK` (default 15) is where the block starts. Weeks earlier than
that are dropped from the table *and* from the summary cards, since a
partial lead-in week would skew the peak-week and 4-week-average numbers.
Raise it if you want more history in view.

All date arithmetic is anchored to UTC midnight rather than local time.
This matters because Nov 1 2026 is the US DST fallback date — doing the math
in local time picks up a 25-hour day and shifts every week number by one.

### Notes

- **No Strava rate limit exposure.** The dashboard reads only the sheet, and
  that read is cached for 120s. Strava is called exactly once per sync run,
  by `strava_to_sheets.py`.
- **Cadence is doubled** to steps-per-minute everywhere, since Strava reports
  per-leg cadence for runs.
- **Fixed histogram buckets**, all declared as constants at the top of the
  `renderHistograms` block in `dashboard.html`:

  | Chart | Constant | Buckets |
  |---|---|---|
  | Distance / activity | `DIST_EDGES_MI` | 0–3, 3–6, 6–10, 10–13, 13–16, 16–20, >20 mi |
  | Moving time / activity | `TIME_EDGES_MIN` | <35, 35–65, 65–100, 100–125, 125–155, 155–185, >185 min |
  | Elevation / activity | `ELEV_EDGES_FT` | <100, 100–200, 200–300, 300–400, 400–500, 500–600, >600 ft |
  | Lap average pace | `PACE_EDGES_MI` | <6:00, 6:00–6:30, 6:30–6:50, 6:50–7:20, 7:20–7:50, 7:50–8:20, 8:20–8:50, 8:50–9:20, 9:20–10:00, >10:00 /mi |

  Two filters drop outliers before binning: `PACE_CLIP_MI` discards laps
  faster than 5:00 or slower than 12:00 min/mi, and `CADENCE_CLIP` discards
  laps below 160 or above 200 spm. Dropped counts are shown next to the
  chart rather than hidden.

  In kilometer mode the distance, elevation and pace edges are converted from
  their imperial values, so both views describe the same real-world numbers.
  Moving time is unit-independent and unchanged.
- **"Runs only" vs "All activities"** changes what feeds the weekly summary
  and the histograms; the recent-activity list always shows everything.
- **Nothing is written.** The server only issues GETs; it can't modify your
  sheet or your Strava data. The service account is requested with read-only
  scopes.

## Publishing the dashboard (Cloudflare Pages + Bear Blog)

`dashboard.html` works in two modes and picks automatically:

- if a `data.json` sits next to it, it runs **fully client-side** from that
  snapshot — no server, no credentials
- if not, it falls back to `/api/sheet` from `serve_dashboard.py`

Both are the same two tables; only the transport differs. So the same file is
both the local dashboard and the published one.

### Why it can't go inside Bear Blog

Bear strips `<script>`, `<object>`, `<embed>` and `<form>` from post and page
content, and only allows `<iframe>` for a whitelist of domains (YouTube,
Vimeo, Spotify, Codepen, Google Docs/Drive/Maps, Bandcamp, Archive.org and a
few more). A self-hosted page isn't on that list, so embedding is out. Bear
subscribers can inject scripts through the header/footer directives in
Settings, but those load on *every* page, which is a fragile way to build one
dashboard.

Instead: host the static build on a subdomain and link to it from Bear's
navigation, which accepts any URL.

### 1. Build the snapshot

```
python build_static.py          # or --demo to try it with no credentials
```

Writes `public/index.html`, `public/data.json` and `public/_headers`.

The snapshot is just the two sheet tabs, trimmed to the columns the dashboard
renders (`ACT_FIELDS` / `LAP_FIELDS` at the top of the file). No Strava calls
are made — the sync step immediately before has already put everything in the
sheet. The build aborts rather than publish if the STRAVA tab comes back
empty, or if a credential-shaped string ever appears in the output.

Because the page holds every activity and lap in memory, the recent-activity
list and its lap splits are just filters over that data — clicking a row
makes no network request at all.

### 2. Deploy on Cloudflare Pages

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git**, pick this repo (private repos are fine on the free tier)
2. Build command: *(leave empty)* · Build output directory: `public`
3. Deploy, then **Custom domains** → add `dash.qrui.xyz`

Cloudflare adds the DNS record itself if `qrui.xyz` is on Cloudflare
nameservers. If it's registered elsewhere, add a `CNAME` for `dash` pointing
at the `*.pages.dev` hostname Cloudflare gives you. Either way `qrui.xyz`
itself stays pointed at Bear — only the `dash` subdomain moves.

### 3. Link it from Bear's navigation

In Bear, **Settings → Navigation**, add a line alongside the existing pages:

```
[Training](https://dash.qrui.xyz)
```

Nav entries are plain markdown links, so an external URL works exactly like
the internal `/about-me/` style links you already have.

### Keeping it fresh

The `strava-sync` job in `.github/workflows/strava_sync.yml` runs
`build_static.py` immediately after the sheet sync — reading back the rows
that step just wrote — and commits `public/` to the repo. Cloudflare Pages
watches the repo and redeploys on that push, so the site updates on the same
twice-daily schedule as the sheet. That ordering is what makes the sheet-only
approach lossless: the snapshot is built from a sheet that is current as of
seconds earlier.

The build step is given **no Strava secrets**, only Google read access.
`RACE_DATE` and `FIRST_WEEK` come from repository **variables** (Settings →
Secrets and variables → Actions → Variables), and fall back to their defaults
if unset.

**Everything in `data.json` is public** once deployed — activity names,
dates, distances, paces, heart rates and cadences. No tokens or keys are ever
written to it. If you later want it private, put Cloudflare Access in front
of the Pages project.

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
