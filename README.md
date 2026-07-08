# TrendCut

A self-hosted weight-and-calorie tracker that estimates your *actual* maintenance
calories (TDEE) from the observed relationship between what you eat and how your
weight moves, smooths daily noise into a stable trend, and tells you when a real
plateau has set in versus when you're just looking at water weight. The estimation
logic is deterministic and mathematically defined, which makes it unusually easy to
test — see [`PRD.md`](PRD.md) for the full design.

> **Status:** v1 complete — the estimation core, the thin API, a deployed
> container, and all four frontend screens are done. The static app (hand-rolled
> HTML/CSS/vanilla JS, no frameworks, no charting library) has a **Dashboard** (TDEE
> hero + from-scratch SVG weight chart + plateau status + pace/projection), a
> **Log Entry** modal, a **Settings** screen (target, smoothing α, regression
> window, kg/lb units), and a **TDEE-over-time** screen (a second hand-rolled chart
> of the rolling estimate's drift — the adaptive-thermogenesis proof). See
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

### Multi-user architecture

The storage layer is fully **user-scoped** and, as of Phase 2, guarded by real
authentication. Every row belongs to a user:

* `users` — `id`, `created_at`, and (Phase 2) `email` + `password_hash` (both NULL
  until an account is bootstrapped).
* `entries` — primary key `(user_id, date)`; `settings` — one row per `user_id`;
  `sessions` — opaque login sessions keyed by the token's SHA-256 hash.
* Foreign keys are enforced (`PRAGMA foreign_keys = ON`), so an entry, settings, or
  session row can never reference a non-existent user.

Every `Storage` method takes an explicit `user_id` — there is no hidden default in
the storage layer. The request's user is resolved at a single seam:
**`get_current_user_id()` in [`api/app.py`](api/app.py)** reads the session cookie
and returns the authenticated user, or raises `401`. Handlers and storage already
thread `user_id`, and the estimator (`estimator/`) stays user-agnostic: storage
fetches one user's series and passes it in exactly as before. See
**[Authentication](#authentication)** for the session model, open signup, and the
CLI bootstrap.

The database is migrated **in place on first startup**, idempotently and inside one
rolled-back-on-failure transaction: a pre-user-scoping DB has its `entries`/
`settings` rebuilt to the composite-PK schema (existing rows backfilled to
`DEFAULT_USER_ID`), and a pre-auth DB gains the `email`/`password_hash` columns and
`sessions` table.

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

### Frontend logic tests

The frontend's **pure** logic (chart coordinate math + its inverse, axis
domain/tick computation, the goal-line anchors, weekly aggregation, unit
conversion, and the honest status→display mapping) is covered by a small suite that
uses **Node's built-in test runner** — no npm, no framework, no build step. The only
enabling file is a one-line [`web/package.json`](web/package.json) that marks the
existing ES-module files as `type: module` so Node can import them (it has zero
dependencies; the browser ignores it).

Run it from the `web/` directory (Node auto-discovers `js/tests/*.test.js`):

```bash
cd web && node --test        # requires Node 18+ (built-in runner)
```

These checks — ruff, pytest, and the frontend tests — are exactly what CI runs on
every push and pull request (see
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
* **Settings** (`/settings.html`) — target weight/date, smoothing strength (α),
  regression window (14/21/28 days), and display units (kg/lb). Saving persists via
  `PUT /settings`; the Dashboard's projection and the estimator's window/α then
  reflect the new values. Units are a display preference only — data is always
  stored in kg and converted at the presentation layer. The Backup card exports all
  entries (CSV/JSON download) and **imports a CSV** — the import button shows a
  confirm step first (import upserts by date, so an imported date *overwrites* that
  day and there is no undo), then displays the result inline: how many entries
  imported, rows skipped and why, any unit assumption made, and how many existing
  entries were overwritten.
* **TDEE over time** (`/tdee.html`) — a second hand-rolled chart of the rolling TDEE
  estimate recomputed as-of each past day (`GET /tdee-history`), showing how
  maintenance drifts down as the body adapts. It honestly breaks the line across
  `insufficient_data` stretches and widens the confidence band on low-confidence
  days rather than drawing a confident line through them.

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
| `GET /tdee-history`      | Rolling TDEE estimate recomputed as-of each past day (drift series) |
| `GET /estimate/requirements` | The estimator's minimum-data bar (for honest onboarding copy) |
| `POST /entries/import`   | Bulk-upsert a CSV body — tolerant flat-CSV parsing (see format below) |
| `GET /entries/export?format=csv\|json` | Dump all entries                                 |
| `POST /auth/register`    | Create an account (email + password), then auto-login          |
| `POST /auth/login`       | Email + password → sets the session cookie                     |
| `POST /auth/logout`      | Invalidate the session and clear the cookie                    |
| `GET /auth/me`           | Is-authenticated probe (never 401s) for the frontend           |

All data routes above require a valid session (a missing/expired session → `401`);
only `/auth/*` and `/health` are reachable unauthenticated. See **Authentication** below.

**Derived, never stored.** Trend weight, TDEE, and the plateau verdict are
recomputed from raw entries on every `/estimate` call — editing or deleting history
always yields a consistent result. The estimator honestly reports
`insufficient_data` / `low_confidence` rather than inventing a number; the API
surfaces those statuses unchanged.

## Authentication

Every data route requires a logged-in session; there is no anonymous access. The
mechanism is deliberately self-host-friendly — **email + password with an opaque
server-side session token in a cookie** — so a cloned repo works with zero external
services (no OAuth provider, no email/SMTP, no JWT signing keys).

**Sign up.** Registration is **open**: anyone who can reach the app can create an
account at `/signup.html` (or `POST /auth/register` with `{email, password}`).
Signup validates the input (a light email check; password 8–1024 chars), creates the
account with its default settings, and **auto-logs-in** — a new user lands
authenticated on the dashboard with an empty history. Passwords need ≥ 8 characters;
emails are normalized (trimmed + lowercased) so casing never mints a duplicate.

> **Signup is unverified and unprotected — three known gaps, all deferred to later
> phases:**
> - **Emails are never verified.** They are login handles only, not confirmed or
>   contactable; no email is ever sent. A typo in your email is not caught.
> - **There is no password reset or account recovery.** A forgotten password or a
>   mistyped signup email is currently **unrecoverable** (short of editing the
>   database directly). Use a password manager.
> - **Open signup has no abuse protection** (no CAPTCHA, no rate limiting), so
>   scripted mass-signup is possible. Fine for a self-host behind a private URL;
>   harden before exposing it widely.
>
> Note also that signup necessarily reveals whether an email is already registered
> (it returns "already registered" so you know why you can't sign up) — a
> user-enumeration oracle. This is an accepted tradeoff of open, no-verification
> signup; **login** stays deliberately generic (same error for unknown email vs.
> wrong password).

**Bootstrap the first account (CLI).** You can also create/claim an account from the
command line — useful for a self-host upgrade, or to attach credentials to data that
predates auth. Run it against the same database the app uses (`TRENDCUT_DB`):

```bash
python -m api.bootstrap_user --email you@example.com          # prompts for the password
```

If an uncredentialed account exists — a user carried over from before auth — the
command **claims it in place**, preserving all its entries and settings; otherwise
it creates a new account. It refuses to overwrite an already-credentialed email.

**Session model.** On login the server generates a random token, stores only its
**SHA-256 hash** (a DB leak yields no usable sessions), and sets it in a cookie that
is `HttpOnly`, `SameSite=Lax`, `Path=/`, and `Secure`. Sessions have a **fixed
30-day** lifetime (not sliding) and are revocable — logout deletes the row and
clears the cookie; expired sessions are purged on login/logout. An expired, missing,
or invalid session returns `401`, and the frontend redirects to `/login.html`.

**Local dev over HTTP.** A `Secure` cookie is never sent over plain
`http://localhost`, so for local development relax it with `TRENDCUT_COOKIE_SECURE=0`
(or `create_app(cookie_secure=False)` in tests). This is an explicit switch — the
app never infers it from the request scheme, so a misconfigured proxy can't silently
drop `Secure` in production.

**Security posture.** Passwords are hashed with argon2id (never stored, logged, or
returned). Login returns one generic message whether the email is unknown or the
password is wrong, and runs a constant-time dummy verification on the unknown-email
path so timing can't reveal which accounts exist. **CSRF:** the app is same-origin
and the session cookie is `SameSite=Lax`, which covers cross-site form POSTs for
now; a dedicated CSRF token is a documented later consideration, not part of this
phase. Basic brute-force throttling / lockout is likewise a later phase.

## Onboarding (first run)

A brand-new account has no data, and TrendCut will **not** show a TDEE number until
there's enough of it to be trustworthy — it shows an honest cold-start state
instead of a fabricated estimate. The dashboard leads a low-data user with an
onboarding card whose primary action is **importing your weight/calorie history**
(the [CSV import](#csv-import-format) below) — the fastest path to a real estimate —
with "log today" as the secondary option.

The messaging is driven by the estimator's *actual* state, so the copy can't drift
from the math:

* With **no entries**, the card invites an import.
* With **some data but not enough**, it shows your real progress — e.g. "you've
  logged 9 of 10 weigh-in days and 9 of 10 intake days" — where the target (10) is
  the estimator's own minimum, read live from `GET /estimate/requirements` (which
  returns `estimator.tdee.MIN_WEIGHT_DAYS` / `MIN_INTAKE_DAYS`), never a hardcoded
  number.
* Once the estimator produces a real estimate (`ok` or `low_confidence`), the
  onboarding card disappears and the normal dashboard stands on its own. An
  established user never sees it.

After an import the dashboard recomputes and either shows a real estimate (if enough
was imported) or the honest "here's where you are, here's what's still needed" state.

### CSV import format

`POST /entries/import` takes a CSV body (the endpoint reads the raw request body,
UTF-8 with optional BOM) — the same endpoint the Settings import button uses. The
parser is **tolerant of the flat date/weight(/calories) exports of popular
weight/nutrition apps** rather than matched to any one format. A header row is
required; matching is case-insensitive, whitespace-trimmed, and BOM-proof:

* **date** — required column; aliases: `date`, `day`, `timestamp`. Values may be
  ISO `YYYY-MM-DD` (with or without a time part), numeric `MM/DD/YYYY` /
  `D/M/YYYY` (day-first is used when the month slot is impossible; a value valid
  both ways is read US month-first *with a warning*), or `Jul 4 2026` /
  `4 Jul, 2026` style — all normalised to ISO.
* **weight** — required column; aliases: `weight`, `weight_kg`, `mass`,
  `body weight`, `Weight (kg)`, `Weight (lb)`, `Weight (lbs)`. A blank cell means
  "no weigh-in" (stored as `NULL`, *not* zero).
* **calories** — optional; aliases: `kcal`, `calories`, `cals`, `energy`,
  `Energy (kcal)`, `Calories (kcal)`. Blank means "not logged".
* Any other columns (body fat, waist, notes, …) are ignored silently.

**Units.** Entries are stored in kg. A weight header that names a unit is trusted:
lb values convert to kg, kg is used as-is. A bare `weight`/`mass` header is
resolved with the *user's current display-units setting* **and a warning is added
naming the assumption** — value ranges are never used to guess, so a silent kg/lb
corruption can't happen.

Handling rules:

* A row with a missing/unparseable `date` is **skipped** and reported in the
  response `warnings`; one bad row never aborts the file. If no date or no weight
  column can be mapped at all, the whole import fails with a warning naming what's
  missing.
* A non-numeric `weight`/`calories` value is treated as missing (blank), with a
  warning. Obvious numeric strings are coerced (e.g. `"1,900"` kcal); a European
  decimal comma (`78,5`) is deliberately *not* misread ×10 — it becomes a warning.
* Fully blank rows are skipped silently; a row with no usable weight *and* no
  usable calories is skipped with a warning.
* Duplicate dates — within the file or already in the DB — **upsert**: the last
  value wins, entries are never duplicated. The response warns how many existing
  entries were overwritten.
* Plausibility guards (weight far outside a human range, negative calories) produce
  soft `warnings` but never reject the row.

**Not supported (by design):** MyFitnessPal's multi-file export zip and meal-split
nutrition files — flat CSV only.

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
| `TRENDCUT_COOKIE_SECURE` | `1` (secure) | Session-cookie `Secure` flag. Set to `0` for local dev over plain HTTP (a `Secure` cookie isn't sent over `http://localhost`). Leave secure in production. |
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

> **Snapshot the volume before deploying the multi-user-readiness release.** The
> first boot of that version runs a one-time, in-place schema migration on the live
> `/data/trendcut.db` (rebuilding `entries`/`settings` to the user-scoped schema).
> It is transactional and idempotent, but take a fresh snapshot first so a rollback
> is trivial if anything looks wrong:
> ```bash
> fly volumes list                     # find the volume id
> fly volumes snapshots create <volume-id>
> ```
> Before running any manual migration/inspection against the file, make sure
> **nothing else holds it open** (a stray `--reload` process, DB Browser, an
> editor's SQLite extension) — a second writer is the usual cause of
> `database is locked`. Inspect with DB Browser in **read-only** mode.

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
