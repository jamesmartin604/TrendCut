# PRD — TDEE & Cut Tracker with Plateau Detection

**Working title:** TrendCut (placeholder)
**Author:** James
**Status:** Draft v1.0
**Last updated:** 29 June 2026
**Type:** Personal project / portfolio piece

---

## 1. Summary

A self-hosted weight-and-calorie tracker that does the one thing generic apps refuse to: it estimates your *actual* maintenance calories (TDEE) from the observed relationship between what you eat and how your weight moves, smooths out daily noise into a stable trend, and tells you when a real plateau has set in versus when you're just looking at water weight.

The core insight is that energy balance is observable. If you log intake and weight for long enough, your true TDEE is implied by the data — you don't need a generic Mifflin–St Jeor estimate that ignores your individual metabolism and your two weeks of adaptive thermogenesis. This app backs out that number and keeps it current.

The estimation logic is deterministic, mathematically defined, and therefore unusually easy to test — which makes it a genuinely good CI showcase, not just a CRUD app with a test folder bolted on.

---

## 2. Problem statement

Standard calorie trackers (MyFitnessPal et al.) have three gaps:

1. **They give you a static TDEE.** It's computed once from height/weight/age/activity and never updates as your body adapts. After two weeks of a cut, adaptive thermogenesis can drop real maintenance by 5–15% below the textbook estimate, and the app has no idea.
2. **They show you raw daily weight.** A single number that jumps ±1 kg overnight from sodium, glycogen, and gut contents. Users either panic at noise or learn to ignore the scale entirely — both bad.
3. **They can't distinguish a stall from a swing.** "I've been the same weight for five days" might mean a true plateau, or might mean you're mid-refeed and holding water. The app can't tell you, so you can't make a decision.

This project solves all three by treating the log as a time series and doing actual estimation on it.

---

## 3. Goals and non-goals

### Goals

- Estimate current TDEE from a rolling window of intake + weight data, and keep it updated daily.
- Produce a smoothed weight trend that strips out day-to-day water/glycogen noise.
- Detect genuine plateaus and surface them with a confidence signal, not a binary guess.
- Be demonstrably correct: the estimator is validated against synthetic data with known ground truth.
- Be a clean, testable codebase with a real CI pipeline (lint → test → deploy on main).
- Run locally / self-hosted with a single deploy. No accounts, no cloud lock-in.

### Non-goals

- **Not** a food database or barcode scanner. You log a daily calorie *total*; the app does not care what you ate. (MFP already does food logging well; reinventing it adds noise and no learning value.)
- **Not** a social / sharing product. Single user.
- **Not** a medical device. It gives estimates for personal decision-making, not clinical advice.
- **Not** multi-metric body composition. Bodyweight only in v1 (no DEXA, no bioimpedance ingestion).
- **Not** mobile-native. Responsive web is sufficient.

---

## 4. Target user

Primary (and only) user for v1: **a data-literate person on a deliberate cut who already logs consistently.** Concretely — someone with a target weight and deadline who weighs in daily and tracks calories, and who is frustrated that the scale and the app aren't giving them an actionable read on whether the diet is working.

Assumptions about this user:
- Will log most days (the estimator tolerates gaps but degrades on sparse data — see §10).
- Understands "trend weight" conceptually or is willing to.
- Wants the *why*, not just a number — surfacing the estimate's uncertainty is a feature, not clutter.

---

## 5. Background: the science the app encodes

This section exists so the requirements aren't arbitrary. The estimator is only as good as the model it assumes.

### 5.1 Energy balance

Over any window, the first law gives:

```
energy_surplus = intake − TDEE = energy stored as Δ body mass
```

Rearranged, **if you can observe intake and the rate of weight change, TDEE is implied:**

```
TDEE = mean_daily_intake − (daily_weight_change_in_kcal)
```

where weight change is converted to energy via an energy-density constant (commonly ~7700 kcal per kg of body-mass change). If you're losing weight, the change term is negative, so estimated TDEE comes out *above* your intake — exactly as it should.

The 7700 figure is a deliberate simplification (pure fat is ~9400 kcal/kg; real weight change mixes fat, lean tissue, and water). It's treated as a tunable constant, and its imperfection is part of why the *trend* matters more than any single day.

### 5.2 Why raw weight is unusable directly

Daily scale weight is dominated by short-term confounders: sodium and carbohydrate intake (glycogen binds ~3 g water per gram), hydration, gut contents, training-induced inflammation. These produce ±0.5–1.5 kg swings that have nothing to do with fat balance. The real signal — fat loss on a cut — is often only ~0.1 kg/day, an order of magnitude smaller than the noise. **Smoothing is not cosmetic; it's the only way to see the signal at all.**

### 5.3 Adaptive thermogenesis

As a cut progresses, the body down-regulates energy expenditure (reduced NEAT, hormonal shifts, lower mass to maintain). A static TDEE estimate becomes progressively wrong. A *rolling* estimate that re-fits on recent data tracks this drift automatically — this is the app's headline advantage over MFP.

### 5.4 Plateau vs. swing

A plateau is a sustained near-zero trend slope *despite a calculated deficit*. A swing is a flat or rising scale weight whose trend slope is still consistent with continued loss once smoothed. The detector's job is to separate these using the smoothed trend and the uncertainty of the fitted slope — not the raw numbers.

---

## 6. Functional requirements

### 6.1 Daily logging

- **FR-1** User can log a daily entry: date, body weight, total calories consumed.
- **FR-2** Both fields independently optional per day (you might weigh in but not finish logging food, or vice versa). The estimator must handle either gap.
- **FR-3** User can edit or delete any past entry; recomputation is automatic.
- **FR-4** Entries are keyed by date; one entry per day (re-logging overwrites).
- **FR-5** Sensible input guards (e.g. weight within a plausible human range, calories ≥ 0) with soft warnings, not hard blocks.

### 6.2 Trend smoothing

- **FR-6** Compute and display an **exponentially weighted moving average (EWMA)** of body weight — the "trend weight" line — alongside raw daily points.
- **FR-7** The smoothing factor (α) is configurable, with a documented sensible default (in the spirit of the Hacker's Diet / TrendWeight lineage, roughly α ≈ 0.1, i.e. a ~10-day effective window). The choice and its effect must be explained in the UI or docs.
- **FR-8** Smoothing must degrade gracefully across missing days (gap-aware: the EWMA should account for elapsed time, not just sequence position).

### 6.3 TDEE estimation

- **FR-9** Estimate current TDEE over a **rolling window** (default 14 days, configurable, e.g. 14/21/28).
- **FR-10** Method: fit a **rolling linear regression** to the *smoothed* weight series to get the slope (kg/day); combine with mean daily intake over the same window via the energy-balance relation in §5.1.
- **FR-11** Surface the estimate's **uncertainty** — at minimum a confidence interval or standard error on the slope, propagated into a TDEE range (e.g. "TDEE ≈ 2,480 kcal, 95% CI 2,310–2,650").
- **FR-12** Display the implied current deficit/surplus (mean intake − estimated TDEE) and the projected rate of loss.
- **FR-13** When data is too sparse or noisy for a trustworthy estimate, the app says so explicitly rather than emitting a confident-but-garbage number (see §10).

### 6.4 Plateau detection

- **FR-14** Flag a plateau when the smoothed-trend slope over the recent window is statistically indistinguishable from zero (its confidence interval includes ~0) **while** the calculated intake implies a meaningful deficit.
- **FR-15** Distinguish a plateau from an expected swing: a flat raw scale with a slope still consistent with loss is **not** flagged.
- **FR-16** Plateau state is graded (e.g. "on track" / "slowing" / "stalled"), not binary.
- **FR-17** When a plateau is flagged, surface the likely interpretation in plain language (e.g. "Your 14-day trend is flat despite an estimated 400 kcal deficit — consistent with adaptive thermogenesis. Your re-estimated TDEE has dropped ~180 kcal since last month.").

### 6.5 Projection (target tracking)

- **FR-18** User can set a **target weight and date**. App computes the required average daily deficit and the on-current-trend projected arrival date.
- **FR-19** Show a simple "are you on pace?" readout that updates as TDEE re-estimates drift.

### 6.6 Visualisation

- **FR-20** A weight chart: raw daily points + EWMA trend line + (optionally) the regression fit over the active window + the target line/date.
- **FR-21** A TDEE-over-time chart showing how the rolling estimate has drifted (this is the visual proof of adaptive thermogenesis).
- **FR-22** Charts rendered in **vanilla JS** on `<canvas>` or lightweight SVG — no charting library dependency. (Explicit learning goal; see §8.)
- **FR-23** Charts are readable on mobile widths.

### 6.7 Data portability

- **FR-24** Import from CSV (so existing MFP / scale-app history can be loaded).
- **FR-25** Export all data to CSV/JSON. The user owns their data; no lock-in.

---

## 7. The estimation engine (design detail)

This is the heart of the project and where most of the learning lives. It is a pure, deterministic, side-effect-free module — given a series in, it returns estimates out, with no I/O. That purity is what makes it CI-friendly.

### 7.1 Pipeline

```
raw daily series (date, weight?, kcal?)
        │
        ▼
[1] gap-handling / alignment        → regular daily index, gaps marked
        │
        ▼
[2] EWMA smoothing on weight         → trend weight series
        │
        ▼
[3] rolling-window selection         → last N days of (trend_weight, intake)
        │
        ▼
[4] linear regression on trend        → slope (kg/day) + standard error
        │
        ▼
[5] energy-balance conversion         → TDEE point estimate + CI
        │
        ▼
[6] plateau classifier                → graded plateau state
```

### 7.2 Key parameters (all configurable, all defaulted)

| Parameter | Default | Notes |
|---|---|---|
| EWMA α | 0.10 | ~10-day effective smoothing |
| Regression window N | 14 days | trade-off: responsiveness vs. stability |
| Energy density | 7700 kcal/kg | tunable; documented simplification |
| Min data threshold | ~10 valid days in window | below this → "insufficient data" |
| Plateau slope band | CI of slope overlaps 0 | with a small dead-zone around 0 |

### 7.3 Robustness requirements

These are the make-or-break behaviours — the naive version is wildly unstable on a bad week:

- The estimate must be **bounded** under realistic noise (a single 2 kg water spike must not throw TDEE by 1000 kcal).
- The estimate must **degrade to an honest "don't know"** rather than emit nonsense when the window is sparse, the slope SE is huge, or intake variance is extreme.
- Re-fitting on each new day must be **stable** — adding one ordinary day shouldn't swing the estimate wildly (no oscillation).
- Outlier resilience: consider a guard against single-day weight outliers (e.g. a robust loss or a winsorising step) — design decision to evaluate, not mandated.

---

## 8. Learning objectives (why this project, explicitly)

These are first-class requirements because the project is partly a portfolio/skills piece:

- **Time-series smoothing** — implement EWMA from scratch; understand α, effective window, and lag/responsiveness trade-offs.
- **Rolling linear regression** — back out a rate from noisy data; propagate slope uncertainty into a final estimate.
- **Signal vs. noise** — turn a series where noise ≫ signal into a stable, decision-grade number. This is the actual intellectual content.
- **Charting from first principles** — vanilla JS on `<canvas>`/SVG, no library. Axes, scaling, redraw on resize.
- **Testing a numerical system** — synthetic ground-truth generation, tolerance assertions, and property-based testing. The estimator's testability is the point.
- **A real CI/CD loop** — lint, test, deploy, on every push to main.

---

## 9. Architecture

A deliberately small stack — the complexity belongs in the estimator, not the plumbing.

- **Estimation engine:** Python. Pure functions over arrays/series (NumPy acceptable; the math is small enough to be legible). No framework coupling. This is the unit under test.
- **Backend / API:** thin Python web layer (FastAPI or Flask) exposing log CRUD + an `/estimate` endpoint that runs the engine and returns trend + TDEE + plateau state as JSON.
- **Storage:** SQLite (single file, zero-config, trivially backed up and exported). One `entries` table is essentially the whole schema.
- **Frontend:** static HTML + vanilla JS. Fetches JSON, renders charts on `<canvas>`/SVG by hand. No SPA framework, no charting lib.
- **Deploy:** single container or single-host deploy, triggered on main.

### 9.1 Data model (v1)

```
entries
  date        TEXT  PRIMARY KEY   (ISO YYYY-MM-DD)
  weight_kg   REAL  NULL
  kcal        INTEGER NULL
  created_at  TEXT
  updated_at  TEXT

settings (single row, or key/value)
  alpha, window_days, energy_density, target_weight, target_date, units
```

Trend weight and TDEE are **derived, not stored** — recomputed from raw entries so that editing history always produces a consistent result.

---

## 10. Edge cases and the central challenge

> **The whole point of the project:** separating real fat-loss signal from water/glycogen swings, and keeping the TDEE regression robust to sparse or noisy input.

Specific cases the system must handle deliberately:

- **A bad week.** Holiday, illness, a refeed — high intake and water retention together. Naive regression reads this as a TDEE collapse. The smoothing + window + uncertainty surfacing must keep this honest (wide CI, "low confidence" flag) rather than confidently wrong.
- **Sparse logging.** Missed days, weight-only days, calorie-only days. Engine must align on a daily index, use what it has, and refuse to estimate below the minimum-data threshold.
- **The first N days.** Before a full window exists, the app shows the trend but withholds (or heavily caveats) the TDEE estimate.
- **Whoosh effect.** Long flat trend then sudden drop (water release). The plateau detector must not declare victory the day before a whoosh, nor panic during the flat stretch — graded states help here.
- **Refeed / diet break.** Intentional intake increase. The deficit readout should reflect reality without the plateau detector misfiring.
- **Unit handling.** kg/lb consistently; the energy-density constant must match.

If the estimator can't be trusted on a noisy week, it isn't done — that robustness is the deliverable, not a nice-to-have.

---

## 11. Testing strategy

The estimator's testability is a headline feature, so the test suite is specified, not left to chance.

### 11.1 Synthetic ground-truth tests (the core)

- Pick a known true TDEE.
- Simulate a daily intake series (with realistic variance).
- Generate the *true* weight trajectory from the energy-balance model.
- Inject realistic noise into the *observed* weight (Gaussian water-weight noise, plus occasional spikes).
- **Assert the estimator recovers the known TDEE within a defined tolerance.**

Run this across a matrix: different true TDEEs, deficit sizes, noise levels, window lengths, and missing-data densities.

### 11.2 Property-based tests (`hypothesis`)

The distinctive, strong addition. Over randomly generated valid series, assert invariants that must *always* hold:

- **Boundedness** — the TDEE estimate stays within physiologically plausible bounds under arbitrary noise (no infinities, no negatives, no 9000-kcal readings).
- **Stability/continuity** — adding one ordinary day doesn't change the estimate by more than a sane amount.
- **Monotonic sanity** — uniformly shifting all intake up by X (everything else equal) shifts the implied deficit consistently.
- **Graceful insufficiency** — on degenerate input (too few points, zero variance), it returns the "insufficient data" state, never a crash and never a false-confident number.
- **Idempotence** — re-running on identical input yields identical output (pure-function guarantee).

### 11.3 Unit + regression tests

- EWMA correctness against hand-computed small cases.
- Regression slope/SE against known fixtures.
- Plateau classifier against curated labelled fixtures (clear plateau, clear loss, mid-swing-not-a-plateau).
- Frozen-snapshot regression tests so estimator changes are deliberate.

### 11.4 API / integration

- CRUD round-trips, CSV import/export round-trips, `/estimate` contract tests.

---

## 12. CI/CD pipeline

```
on push / PR:
  ├── lint        (ruff / flake8 + black --check ; JS linted too)
  ├── pytest      (unit + synthetic + hypothesis property tests)
  └── [main only] deploy
```

- **Lint gate** — style + static checks fail the build.
- **Test gate** — full pytest suite including the `hypothesis` properties; must pass to merge.
- **Deploy on main** — green main auto-deploys to the self-hosted target.
- Test coverage on the estimation module specifically should be high and visible (the engine is the asset).

---

## 13. Success metrics

This is a personal tool, so "success" is functional and pedagogical rather than commercial:

- **Estimator accuracy:** recovers known TDEE within tolerance (e.g. ±100 kcal) on synthetic data across the test matrix.
- **Estimator robustness:** all `hypothesis` invariants hold; no crashes or absurd outputs on adversarial noise.
- **Decision usefulness:** on real personal data, the app gives an actionable read on the late-August target — i.e. it correctly flags the adaptive-thermogenesis plateau and shows the re-estimated TDEE drifting down, where MFP showed nothing.
- **Engineering quality:** green CI on every main commit; high coverage on the engine; charts rendered with zero charting dependencies.

---

## 14. Milestones

**M1 — Logging + storage.** Daily CRUD, SQLite, CSV import of existing history. (Get real data flowing in.)

**M2 — Smoothing + chart.** EWMA trend weight, vanilla-JS weight chart with raw points + trend line. (First moment it beats the scale.)

**M3 — TDEE estimator + tests.** Rolling regression, energy-balance conversion, uncertainty. Synthetic ground-truth tests + first `hypothesis` properties. CI pipeline stood up here.

**M4 — Plateau detection.** Graded classifier, plain-language surfacing, swing-vs-stall logic.

**M5 — Projection + polish.** Target weight/date, on-pace readout, TDEE-over-time chart, export, robustness hardening on a "bad week" dataset.

---

## 15. Future enhancements (out of scope for v1)

- Macro / sodium logging to model water-weight confounds explicitly.
- Bayesian state-space / Kalman-filter estimator (model fat mass and water mass as separate latent states) — a more principled successor to the rolling regression.
- Per-day weight-in-time-of-day normalisation.
- Multiple cut/bulk phases with segmented estimation.
- Native mobile wrapper / PWA for one-tap morning weigh-in.
- Refeed and diet-break detection as first-class events.

---

## 16. Open questions

- Energy-density constant: fixed 7700, or let the system *learn* an effective constant from the user's own long-run intake/loss data?
- Best default window length — 14 days is responsive but noisier; 28 is stable but lags adaptation. Possibly adaptive.
- Outlier handling: robust regression vs. explicit winsorising vs. leave-it-to-the-EWMA — needs an empirical decision on real data.
- Plateau threshold calibration: how flat, for how long, before flagging — tune against labelled personal history.
