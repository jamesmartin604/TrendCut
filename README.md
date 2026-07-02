# TrendCut

A self-hosted weight-and-calorie tracker that estimates your *actual* maintenance
calories (TDEE) from the observed relationship between what you eat and how your
weight moves, smooths daily noise into a stable trend, and tells you when a real
plateau has set in versus when you're just looking at water weight. The estimation
logic is deterministic and mathematically defined, which makes it unusually easy to
test — see [`PRD.md`](PRD.md) for the full design.

> **Status:** Phase 6 — the estimation core plus a thin API over it are complete.
> A framework-free SQLite storage layer and a FastAPI app expose entry CRUD,
> settings, CSV import/export, and an `/estimate` endpoint that recomputes the trend,
> TDEE, and plateau verdict from raw entries on every call (nothing derived is ever
> stored). See [Running the API](#running-the-api) below. The estimation core:
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

## Running the API

The `api/` package is a thin FastAPI + SQLite layer over the pure estimator. Start
it with uvicorn (after `pip install -e ".[dev]"`):

```bash
uvicorn api.app:app --reload
```

It serves on `http://127.0.0.1:8000` and writes to `./trendcut.db` by default;
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
