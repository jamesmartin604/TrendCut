# TrendCut

A self-hosted weight and calorie tracker that estimates your *actual* maintenance
calories (TDEE) from how your weight responds to what you eat. It smooths daily
noise into a stable trend and tells you whether a plateau is real or just water
weight.

The estimation logic is deterministic and mathematically defined, so it can be
tested against synthetic data with a known answer. The results are published on the
app's public [validation page](#validation).

## Features

- **TDEE estimate with a confidence interval.** If there isn't enough data, it
  says so instead of showing a made-up number.
- **Weight trend chart.** Faint raw weigh-ins, a bold smoothed trend line, and a
  dashed target line. Ranges: 7 / 30 / 90 days and All. Hand-rolled SVG, no
  charting library.
- **Plateau detection.** A graded verdict (on track, slowing, stalled) with a
  plain-language explanation.
- **Pace and projection.** Implied daily deficit, projected rate of change, and
  whether you're on pace for your target date.
- **TDEE over time.** A chart of how your estimated maintenance drifts as your
  body adapts.
- **Show-your-work math.** Every TDEE has a "How we calculated this" panel showing
  the real intermediate values behind that number, plus the method's known
  limitations.
- **CSV import and export.** Bring your history from other apps and take it with
  you when you leave.
- **Optional day-1 estimate.** A clearly labelled generic estimate
  (Mifflin-St Jeor) shown only until you have enough data for the real one.
- **No frameworks on the frontend.** Plain HTML, CSS, and vanilla JS modules, with
  no build step.

## How it works

TrendCut smooths your weigh-ins with a gap-aware exponentially weighted moving
average, then regresses the trend over a rolling window:

```
TDEE = mean intake − trend slope × energy density
```

Energy density defaults to 7,700 kcal/kg, a population average. Trend weight, TDEE,
and the plateau verdict are always recomputed from your raw entries and never
stored, so editing or deleting history always gives a consistent result.

**Limitations, stated up front:**

- The confidence interval is likely optimistic. It comes from a regression on an
  already-smoothed series, so treat it as indicative rather than exact.
- 7,700 kcal/kg is an average. Real weight change mixes fat, lean mass, and water.
- Smoothing removes daily noise but lags real changes.
- The estimator needs a minimum amount of data (roughly 10 weigh-in days and 10
  intake days) and declines to give a number below that.

TrendCut is an informational tool, not medical advice. Talk to a qualified
professional before making significant changes to your diet.

## Quickstart

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m api.migrate              # create or upgrade the schema
TRENDCUT_COOKIE_SECURE=0 uvicorn api.app:app --reload
```

Open <http://127.0.0.1:8000/>, create an account at `/signup.html`, and start
logging. Interactive API docs are at `/docs`.

- `TRENDCUT_COOKIE_SECURE=0` is for local development over plain HTTP only. A
  `Secure` cookie is never sent over `http://localhost`. Leave it unset in
  production.
- Schema migration is an explicit step. On boot the app verifies the schema and
  refuses to start if it is out of date, telling you to run `python -m api.migrate`.
  Migration is idempotent and runs in a single transaction that rolls back on
  failure.

## Importing and exporting data

Upload a CSV from **Settings → Backup**, or `POST` it to `/entries/import` while
logged in. The parser accepts flat date/weight(/calories) exports from most weight
and nutrition apps.

```csv
date,weight_kg,kcal
2026-06-01,80.4,2100
2026-06-02,,2050
2026-06-03,80.1,
```

- A header row is required. Column matching is case-insensitive.
- `date` and `weight` are required; `calories` is optional. Common aliases are
  recognised (e.g. `Weight (lb)`, `Energy (kcal)`).
- Dates may be ISO (`2026-06-01`), numeric (`06/01/2026`, `1/6/2026`), or text
  (`Jul 4 2026`). An ambiguous numeric date is read month-first, with a warning.
- Blank cells mean "not logged", not zero.
- If a weight header names a unit, it is trusted. A bare `weight` column uses your
  display-units setting and a warning tells you so. Units are never guessed from the
  values.
- Importing **overwrites** any existing entries on the same dates, and there is no
  undo.
- Bad rows are skipped and reported. One bad row never aborts the file.

Not supported: multi-file export zips and meal-split nutrition files. Flat CSV only.

Export from **Settings → Backup** (CSV or JSON). Exports use the same
`date,weight_kg,kcal` columns, so they re-import cleanly. An export is the simplest
backup of your data, so it's worth taking one regularly.

## Accounts

Every data route requires a logged-in session. Accounts use an email and password,
with an opaque server-side session token in an `HttpOnly`, `SameSite=Lax`, `Secure`
cookie. There's no external auth provider to set up. Passwords are hashed with
argon2id.

- **Sign up** is open at `/signup.html`. Passwords must be 8 to 1024 characters.
- **Sessions** last 30 days and can be revoked by logging out.
- **Password reset** works two ways: by emailed link if you configure SMTP, or by
  an operator generating a link from the command line:
```bash
  python -m api.reset_password --email you@example.com --base-url https://your-host
```
- **Creating or claiming an account from the CLI:**
```bash
  python -m api.bootstrap_user --email you@example.com     # prompts for the password
```
  If an account without credentials already exists (for example, one carried over
  from before auth was added), this claims it in place and keeps its entries and
  settings.

Run both CLI commands against the same database the app uses (`TRENDCUT_DB`).

> **Running a public instance?** Signup does not verify email addresses, and the
> account endpoints have no built-in rate limiting. If you expose an instance to
> the internet, put it behind a reverse proxy that rate-limits `/auth/*`, and serve
> it over HTTPS only.

## Configuration

Everything has a default, so no configuration is needed to run locally.

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRENDCUT_DB` | `trendcut.db` | Path to the SQLite file. |
| `TRENDCUT_COOKIE_SECURE` | `1` | Session cookie `Secure` flag. Set `0` for local HTTP only. |
| `HOST` | `0.0.0.0` | Interface the container binds. |
| `PORT` | `8080` | Port the container binds. |
| `TRENDCUT_SMTP_HOST` | unset | SMTP host for password-reset email. If set, `TRENDCUT_BASE_URL` is required. Unset means reset links come from the CLI instead. |
| `TRENDCUT_SMTP_PORT` | `587` | SMTP port. |
| `TRENDCUT_SMTP_USER` / `TRENDCUT_SMTP_PASSWORD` | unset | SMTP credentials (optional). |
| `TRENDCUT_SMTP_FROM` | `no-reply@trendcut.local` | From address. |
| `TRENDCUT_SMTP_STARTTLS` | `1` | Use STARTTLS. |
| `TRENDCUT_BASE_URL` | unset | Public base URL used in reset links. Required when SMTP is configured; the app refuses to boot without it. |

Estimator settings (smoothing α, regression window, energy density, target, units)
are per-user and live in the app's **Settings** screen, not in environment
variables.

If you use a transactional email provider for reset emails, turn **off** open
tracking and link tracking. Link tracking rewrites the reset URL through the
provider's redirector, which adds a tracked third-party hop between the user and
their token.

## Validation

`/validation.html` is a public page showing how accurately the estimator recovers a
**known** TDEE from synthetic data. No account is needed.

- **Real runs.** `python -m api.validation_report` runs a seeded scenario matrix
  through the real simulator and estimator and writes `web/validation.json`. The
  page reads that static file, so the estimator never runs live on the public
  endpoint.
- **Drift-proof.** The file is committed, and a test regenerates and compares it in
  CI, so the published numbers can't silently diverge from the code.
- **Weak spots included.** The sparse, noisy scenario is shown at its real
  sub-100% pass rate. Tolerances are set in advance and never widened to make
  everything pass.
- **Not circular.** The page shows that driving the simulator with an energy
  density different from the estimator's assumed 7,700 kcal/kg produces the
  expected physical bias.
- **No user data.** The harness imports only the estimator modules, never storage,
  and a test enforces this.

## Deployment

TrendCut ships as a small container (see the [`Dockerfile`](Dockerfile)) that runs
uvicorn and serves the API and static frontend same-origin. The SQLite database
should live on a persistent volume, set via `TRENDCUT_DB`.

- **Run a single instance.** SQLite is single-writer, so don't scale out.
- **Migrate explicitly.** Boot never changes your schema. Run
  `python -m api.migrate` against the volume before starting a new release, and
  snapshot the volume first.
- **Health check.** `GET /health` is unauthenticated and suitable for load balancer
  or platform checks.

The repo includes a [`fly.toml`](fly.toml) and a GitHub Actions workflow that
deploys to [Fly.io](https://fly.io) on a green push to `main`. Treat them as a
working example to adapt rather than a requirement.

## Project layout

```
estimator/   # pure, framework-free estimation engine (smoothing, TDEE, plateau, synthetic data)
api/         # thin FastAPI + SQLite layer: entries, settings, /estimate, auth, CSV
web/         # static frontend: vanilla JS/HTML/CSS, hand-rolled SVG charts
examples/    # runnable demos of each estimator stage
tests/       # pytest + hypothesis suite
```

## Development

```bash
ruff check .              # lint
ruff format --check .     # formatting
pytest                    # backend tests (property tests use the fast "ci" profile)
cd web && node --test     # frontend logic tests (Node 18+, no npm install needed)
```

For heavier fuzzing, run the property suite with `HYPOTHESIS_PROFILE=deep` (2,500
examples per test instead of 150). In PowerShell:

```powershell
$env:HYPOTHESIS_PROFILE="deep"; pytest tests/test_properties.py -q; Remove-Item Env:HYPOTHESIS_PROFILE
```

CI runs lint, pytest, and the frontend tests on every push and pull request.

The [`examples/`](examples/) folder has runnable demos of each estimator stage:
generating a noisy diary, smoothing it, recovering the hidden TDEE, and telling a
stall apart from a water-weight swing.

## Acknowledgements

The password-reset email template is adapted from Postmark's MIT-licensed
transactional email templates, with the copy changed for TrendCut. The original
copyright and license notice are preserved in `api/reset_email.py`.

## License

MIT-license