# TrendCut

A self-hosted weight-and-calorie tracker that estimates your *actual* maintenance
calories (TDEE) from the observed relationship between what you eat and how your
weight moves, smooths daily noise into a stable trend, and tells you when a real
plateau has set in versus when you're just looking at water weight. The estimation
logic is deterministic and mathematically defined, which makes it unusually easy to
test — see [`PRD.md`](PRD.md) for the full design.

> **Status:** Phase 7 — the estimation core, the thin API, and the first frontend
> screens are complete. A static dashboard (hand-rolled HTML/CSS/vanilla JS, no
> frameworks, no charting library) renders the TDEE hero with its confidence range,
> a from-scratch SVG weight-trend chart, the graded plateau status, and a pace
> readout, plus a log-entry modal — all served by the API itself. See
> [Running the app](#running-the-app) below. The estimation core:
> the synthetic
> ground-truth generator (`estimator/synthetic.py`), the gap-aware EWMA trend
> smoother (`estimator/smoothing.py`), the rolling-regression TDEE estimator
> (`estimator/tdee.py`), and the graded plateau classifier
> (`estimator/plateau.py`) are all implemented and tested. The estimator recovers a
> known TDEE from noisy synthetic data within ±100 kcal (matched energy density,
> moderate noise), a `hypothesis` property-based suite (`tests/test_properties.py`)
> fuzzes its robustness invariants, and the plateau classifier distinguishes a
> genuine stall from a water-weight swing. Remaining work is plumbing (API, storage,
> frontend, deploy). See [`examples/`](examples/) for runnable demos —
> [`simulate_demo.py`](examples/simulate_demo.py) generates a noisy diary,
> [`smoothing_demo.py`](examples/smoothing_demo.py) charts the trend line through it,
> [`tdee_demo.py`](examples/tdee_demo.py) recovers the hidden TDEE, and
> [`plateau_demo.py`](examples/plateau_demo.py) tells a stall apart from a swing.

## Layout

```
estimator/      # pure, framework-free estimation engine (the unit under test)
  smoothing.py  # gap-aware EWMA trend weight
  tdee.py       # rolling regression + energy-balance TDEE estimate
  plateau.py    # graded plateau classifier
  synthetic.py  # synthetic ground-truth data generation for tests
api/            # thin FastAPI + SQLite layer (entry CRUD, settings, /estimate, CSV)
web/            # static frontend: dashboard + log modal, hand-rolled SVG chart
  js/chart-math.js  # pure coordinate/tick math (no DOM), consumed by chart.js
examples/       # runnable demos (simulate/smoothing/tdee/plateau _demo.py)
tests/          # pytest + hypothesis suite
```

## Development setup

Requires Python 3.12+.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1

# 2. Install the package with its dev dependencies
pip install -e ".[dev]"
```

## Checks

```bash
ruff check .              # lint
ruff format --check .     # formatting check
pytest                    # tests (property tests use the fast "ci" hypothesis profile)
```

For heavier local fuzzing, run the property suite under the deep profile
(2500 examples/test instead of 150) via the `HYPOTHESIS_PROFILE` env var — in
PowerShell:

```powershell
$env:HYPOTHESIS_PROFILE="deep"; pytest tests/test_properties.py -q; Remove-Item Env:HYPOTHESIS_PROFILE
```

These three are exactly what CI runs on every push and pull request (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Running the app

The `api/` package is a thin FastAPI + SQLite layer over the pure estimator, and it
also serves the static frontend in `web/` (no build step — plain HTML, hand-rolled
CSS, and vanilla-JS ES modules). Start it with uvicorn (after
`pip install -e ".[dev]"`):

```bash
uvicorn api.app:app --reload
```

Then open **http://127.0.0.1:8000/** in a browser for the dashboard:

* **TDEE hero** — the current estimate with its 95% CI, honestly downgraded to
  "Not enough data yet" (`insufficient_data`) or visibly flagged
  (`low_confidence`) when the estimator declines to commit.
* **Weight trend chart** — hand-rolled SVG (no charting library, PRD FR-22): faint
  raw weigh-ins, the bold EWMA trend line, a dashed target line, 7/30/90-day/All
  ranges.
* **Plateau status + pace** — the graded on-track/slowing/stalled verdict with the
  estimator's plain-language explanation, the implied daily deficit, projected
  rate, and an on-pace-for-target readout.
* **Log entry** — the "Log entry" button (or the + button on mobile) opens a modal
  for date + weight + calories; either field may be left blank (weight-only /
  calorie-only days). Soft input-guard warnings from the API are shown inline
  without blocking the save; picking a date with an existing entry prefills it for
  editing.

The API serves on `http://127.0.0.1:8000` and writes to `./trendcut.db` by default;
override the DB path with the `TRENDCUT_DB` environment variable. Interactive API
docs are at `http://127.0.0.1:8000/docs`.

Key endpoints:

| Method & path            | Purpose                                                        |
|--------------------------|----------------------------------------------------------------|
| `PUT /entries/{date}`    | Create/overwrite a day (`{"weight_kg": .., "kcal": ..}`, both optional) |
| `GET /entries/{date}`    | Read one day                                                    |
| `GET /entries?start=&end=` | List all days (optionally date-ranged)                       |
| `DELETE /entries/{date}` | Delete a day                                                    |
| `GET /settings` / `PUT /settings` | Read / partially update α, window, energy density, target, units |
| `GET /estimate`          | Recompute trend + TDEE (point, CI, status) + plateau verdict   |
| `POST /entries/import`   | Bulk-upsert a CSV body (see format below)                      |
| `GET /entries/export?format=csv\|json` | Dump all entries                                 |

**Derived, never stored.** Trend weight, TDEE, and the plateau verdict are
recomputed from raw entries on every `/estimate` call — editing or deleting history
always yields a consistent result. The estimator honestly reports
`insufficient_data` / `low_confidence` rather than inventing a number; the API
surfaces those statuses unchanged.

### CSV import format

`POST /entries/import` takes a CSV body (the endpoint reads the raw request body,
UTF-8 with optional BOM). A header row is required; columns are case-insensitive:

* `date` — **required**, ISO `YYYY-MM-DD`.
* `weight_kg` (alias `weight`) — optional; a blank cell means "no weigh-in" (stored
  as `NULL`, *not* zero).
* `kcal` (aliases `calories`, `cals`) — optional; blank means "not logged".

Handling rules:

* A row with a missing/unparseable `date` is **skipped** and reported in the
  response `warnings` (the import does not fail).
* A non-numeric `weight_kg`/`kcal` is treated as missing (blank), with a warning.
* Duplicate dates — within the file or already in the DB — **upsert**: the last
  value wins, entries are never duplicated.
* Plausibility guards (weight far outside a human range, negative calories) produce
  soft `warnings` but never reject the row.

Example:

```bash
curl -X POST --data-binary @history.csv http://127.0.0.1:8000/entries/import
```

```csv
date,weight_kg,kcal
2026-06-01,80.4,2100
2026-06-02,,2050
2026-06-03,80.1,
```

`GET /entries/export?format=csv` writes the canonical `date,weight_kg,kcal` columns
back out, so an export round-trips cleanly through import.

## Configuration (environment variables)

Every knob has a sensible default, so the app runs with no configuration at all.

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRENDCUT_DB` | `trendcut.db` | Path to the SQLite file. In the container this is `/data/trendcut.db`, on the mounted volume. |
| `HOST` | `0.0.0.0` | Interface uvicorn binds (used by the container start command). |
| `PORT` | `8080` | Port uvicorn binds (matches Fly's `internal_port`). |

(Estimator/plan knobs — α, window, energy density, target, units — are **not** env
vars; they live in the `settings` table and are edited via `PUT /settings`.)

## Deployment (Fly.io)

The app ships as a small container (see [`Dockerfile`](Dockerfile)) running uvicorn
and serving the API + static frontend same-origin. It deploys to
[Fly.io](https://fly.io) with the SQLite database on a **persistent volume** that
outlives redeploys.

### One-time setup

1. Install [flyctl](https://fly.io/docs/flyctl/install/) and `fly auth login`.
2. Pick a unique app name and your nearest region, then edit `app` and
   `primary_region` in [`fly.toml`](fly.toml) (or run `fly launch --no-deploy` to
   have Fly fill them in — keep the existing `[[mounts]]` and `[http_service]`).
3. Create the persistent volume (once) in the same region:
   ```bash
   fly volumes create trendcut_data --size 1 --region <your-region>
   ```
   This 1 GB volume holds `trendcut.db`. It is **not** part of the image, so
   rebuilding/redeploying the app never touches it.
4. Keep it to a single machine — SQLite is single-writer:
   ```bash
   fly scale count 1
   ```

### CI secret you must set

The deploy job in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) needs one
GitHub Actions secret, which **you** populate (nothing secret is committed):

| Secret name | How to create it |
|-------------|------------------|
| `FLY_API_TOKEN` | `fly tokens create deploy` — copy the output into GitHub → *Settings → Secrets and variables → Actions → New repository secret*. |

### How deploys run

* Every push and PR runs **lint + test** (`lint-and-test` job).
* The **`deploy` job runs only on a push to `main`, and only after `lint-and-test`
  passes** (`needs: lint-and-test`). A failing test blocks the deploy; PRs and
  non-`main` branches never deploy.
* On a green `main` push, CI runs `flyctl deploy --remote-only`, which builds the
  image on Fly and rolls the machine over to it. The volume — and your data —
  stays put. Fly health-checks `GET /health` before sending traffic.

You can also deploy by hand at any time with `fly deploy`.

### Backups and restore

Your logged data is the thing that matters, so keep copies. Two levels:

**Logical (recommended, human-readable).** The app's own export is the simplest
backup — run it on a schedule and keep the files:
```bash
curl https://<your-app>.fly.dev/entries/export?format=csv  > backup-$(date +%F).csv
# restore into any instance:
curl -X POST --data-binary @backup-2026-07-04.csv https://<your-app>.fly.dev/entries/import
```
Because import upserts by date, restoring is idempotent.

**Full database file.** For a byte-exact copy, snapshot the SQLite file over SSH
(`.backup` takes a consistent copy of a live DB):
```bash
fly ssh console -C "sqlite3 /data/trendcut.db \".backup '/data/backup.db'\""
fly ssh sftp get /data/backup.db ./trendcut-$(date +%F).db
# restore: push a .db back and restart
fly ssh sftp shell   # -> put ./trendcut-2026-07-04.db /data/trendcut.db
fly apps restart <your-app>
```
Fly also snapshots volumes automatically (retention configurable via
`fly volumes snapshots`), but app-level exports are your portable, host-independent
safety net.
