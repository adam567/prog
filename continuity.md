# PH3 Tracker — Continuity

## What This Is
A single-page PWA workout tracker based on Layne Norton's PH3 (Power Hypertrophy 3) program.
Deployed at **adam567.github.io/prog** via GitHub Pages from the **adam567/prog** repo (main branch).

## Architecture (since 2026-11-14)
- **Live at https://ph3.azurewebsites.net** (personal Azure: subscription PH3, rg ph3-rg, Free F1 Linux plan ph3-plan, westus2, $20 budget ph3-20). The old GitHub Pages copy (adam567.github.io/prog) is the pre-sync version.
- `index.html`: the whole PWA (HTML/CSS/JS). localStorage is the offline working copy; every change is pushed to the server (`Sync` object, last-write-wins per document).
- `app.py`: Python stdlib server. Serves the static files, `GET /api/data`, `PUT /api/data/<key>` (Bearer sync key), keeps every write in a `history` table, and handles Strava OAuth + activity/heart-rate import. SQLite at `/home/data/ph3.db` (app setting `PH3_DB`).
- Synced documents: `profile` (incl. `cardio` goals), `program` (incl. `ramp` ease-in weeks), `workouts`, `cardio`, `stravaGym`, `activeWorkout`.
- Secrets: `PH3_KEY` (1Password "PH3 sync key"); `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` once the Strava API app exists. App settings only.

## Deploy Process
Zip exactly: app.py, index.html, clicker.html, manifest.json, sw.js, robots.txt, startup.sh. Then (PowerShell):
```
$env:AZURE_CONFIG_DIR='C:\wine\.azure-personal'; az account show   # must be PH3 / personal account
az webapp deploy -n ph3 -g ph3-rg --type zip --src-path <zip>
```
No build step (stdlib only, SCM_DO_BUILD_DURING_DEPLOYMENT=false). Startup: `bash startup.sh`.

## Key Features (as of 2026-03-18)
- **13-week periodized program** across 4 blocks: Accumulation, Transmutation, Realization, Peaking
- **Branded PH3 header** with blue logo badge on all pages
- **Set timer flow**: Play (▶) → 5s countdown → TUT timing → Stop (■) marks complete
  - Cancel during countdown by tapping again
  - TUT (time under tension) recorded per set in seconds
  - Tap ✓ to undo a completed set
- **+ Add Set** button on all exercises (main and accessory)
- **Band tracking for Pullups**: None / Green (-40 lb) / Black (-65 lb) with effective load display
- **Bodyweight** stored per workout record for volume calculations
- **Rest timer** with nudge alerts, avg/max stats in timer bar
- **Clear Workout History** button in Settings (keeps program/maxes)
- **Exercise catalog** for adding custom accessories mid-workout

## User Profile
- Current bodyweight: 245 lb
- Assistance bands: Green (40 lb), Black (65 lb)
- Squat/Bench/Deadlift tracked as main compound lifts

## Recent Changes (this session)
1. Fixed set row overflow cutting off right margin on mobile
2. Added branded PH3 header with logo to all views
3. Added "+ Add Set" button on all exercises
4. Added band tracking for pullups with effective load calc
5. Added "Clear Workout History" button
6. Bodyweight recorded in workout history
7. Replaced checkboxes with play/stop TUT timer per set
8. Added cancel during countdown phase
9. Fixed stop button not marking set complete after countdown
