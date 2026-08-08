"""Confidence-interval CALIBRATION for the TDEE estimator — a standing guardrail.

The rest of the suite asks "is the point estimate close to the truth?"
(``test_tdee.py``, ``api/validation_report.py``). This module asks the different and
currently unflattering question: **when the estimator says "2400 +/- 39 kcal, status
ok", how often is the truth actually inside that interval?**

Method
------
Drive the frozen simulator from a *constant* hidden TDEE with flat intake, so the
true TDEE is flat by construction and the correct output is a flat line. Replay
``estimate_tdee`` day by day exactly as the app does, and count the fraction of daily
95% intervals that contain the known truth. Nominal coverage is 0.95.

Measured behaviour, 400 seeds x 11 replayed days = 4400 day-estimates per window
(the numbers the assertions below are calibrated against):

    window_days  coverage   mean +/-CI   +/-CI needed for 95%   too narrow by   'ok'
        14         0.255      64 kcal          359 kcal             5.6x        100%
        21         0.253      48                305                 6.4x        100%
        28         0.230      39                295                 7.6x        100%
        42         0.174      35                314                 9.0x        100%

Regenerate this table with ``python -m tests.test_ci_coverage``.

Why it is this bad (a KNOWN GAP, not a surprise)
------------------------------------------------
``estimator/tdee.py`` already documents it: the OLS standard error assumes
independent residuals, but the regression runs on an already-EWMA-smoothed and
therefore strongly autocorrelated series, so the SE understates the true uncertainty
(PRD §7.3). Measured residual lag-1 autocorrelation on real data runs +0.6 to +0.8.

Note the gap is *larger* than an autocorrelation-aware SE alone would close: a
Newey-West correction widens the SE by only ~1.2-1.9x, against the 5.6x-9.0x measured
here. The remainder is window-composition sensitivity — the reported interval
describes within-window fit uncertainty only, and knows nothing about how much the
estimate moves when the window rolls forward and old days age out.

What the tests here do about it
--------------------------------
* :func:`test_coverage_is_in_the_documented_band` is a CHARACTERIZATION test. It
  passes today and fails if coverage moves materially in EITHER direction. An
  improvement failing is the point: the number above cannot be changed silently.
  (Same spirit as ``test_validation_report.py``'s ``pass_rate < 1.0`` assertion on
  the deliberately weak scenario — the weakness is published, not hidden.)
* :func:`test_reported_ci_achieves_nominal_coverage` is an ``xfail(strict=True)``
  carrying the real 0.95 target. It is green today *as an expected failure*; the day
  someone fixes the SE it turns red as an XPASS, forcing it to be promoted to a
  plain assertion. The target is executable, not a comment.

Determinism
-----------
Every run is explicitly seeded, and the assertions are BANDS rather than exact
floats, for two reasons: coverage varies ~+/-0.02 across disjoint seed blocks, and
NumPy does not guarantee ``Generator`` stream stability across versions (unlike the
legacy ``RandomState``). Do not tighten these into exact-value assertions.

Cross-platform float safety is not a concern here despite the hit/miss counting: the
closest approach of the truth to any interval edge measured across 2200 estimates was
0.176 kcal, which is ~10^12 ULPs from a float64 boundary at this magnitude.

Anti-circularity
----------------
``true_energy_density`` belongs to the simulator and is never passed into
``estimate_tdee``, which uses its own ``assumed_energy_density``. The primary
scenario leaves both at their own defaults so this measures interval width, not
density bias — that bias is covered by ``test_density_mismatch_biases_as_expected``.
"""

from __future__ import annotations

import numpy as np
import pytest

from estimator.synthetic import simulate
from estimator.tdee import estimate_tdee

# --- scenario: a constant hidden TDEE, so the correct answer is a FLAT line -------
TRUE_TDEE = 2400.0
INTAKE = 2000.0  # 400 kcal/day deficit -> ~0.57 kg over the replayed stretch
START_WEIGHT = 77.0
PREFIX_DAYS = 28  # EWMA warm-up + a full window before the first replayed day
REPLAY_DAYS = 11
N_SEEDS = 400
WINDOWS = (14, 21, 28, 42)

NOMINAL_COVERAGE = 0.95  # what a 95% interval is supposed to deliver

# Characterization band. Deliberately wide: its job is to catch a MATERIAL change in
# the estimator's honesty, not to pin a float. Today's values span 0.174-0.255, and
# coverage wobbles ~+/-0.02 across disjoint seed blocks.
COVERAGE_BAND = (0.10, 0.45)

# The actionable engineering target a future fix must move: reported half-width vs
# the half-width that would actually have delivered 95%. Today 5.6x-9.0x.
MIN_NARROWNESS_FACTOR = 3.0


def measure_coverage(window_days: int, n_seeds: int = N_SEEDS) -> dict[str, float]:
    """Empirical 95%-CI coverage against a known, constant true TDEE.

    Returns ``coverage`` (fraction of daily intervals containing the truth),
    ``mean_halfwidth`` (what the estimator reported), ``needed_halfwidth`` (the 95th
    percentile of the absolute error, i.e. what it would have had to report to be
    right 95% of the time), their ratio, and the share of days flagged ``ok``.
    """
    hits = 0
    ok_days = 0
    halfwidths: list[float] = []
    abs_errors: list[float] = []

    for seed in range(n_seeds):
        sim = simulate(
            n_days=PREFIX_DAYS + REPLAY_DAYS,
            true_tdee=TRUE_TDEE,
            intake=INTAKE,
            start_weight=START_WEIGHT,
            # `true_energy_density` is deliberately NOT passed: the simulator keeps
            # its own default and the estimator keeps its own `assumed_energy_density`.
            # The two constants are never shared (anti-circularity).
            water_sd=0.5,
            water_autocorr=0.7,
            spike_rate=0.03,
            spike_magnitude=1.0,
            seed=seed,
        )
        weights = sim.observed.weight
        intake = sim.observed.intake

        for k in range(PREFIX_DAYS, PREFIX_DAYS + REPLAY_DAYS):
            # The estimator sees only history up to day k, exactly as the app does,
            # and keeps its own assumed_energy_density.
            est = estimate_tdee(weights[: k + 1], intake[: k + 1], window_days=window_days)
            if est.tdee is None:
                continue
            halfwidths.append(est.ci_high - est.tdee)
            abs_errors.append(abs(est.tdee - TRUE_TDEE))
            ok_days += int(est.status == "ok")
            hits += int(est.ci_low <= TRUE_TDEE <= est.ci_high)

    n_est = len(halfwidths)
    assert n_est > 0, "scenario produced no estimable days — check PREFIX_DAYS/thresholds"
    mean_hw = float(np.mean(halfwidths))
    needed_hw = float(np.percentile(abs_errors, 100 * NOMINAL_COVERAGE))
    return {
        "window_days": float(window_days),
        "n_estimates": float(n_est),
        "coverage": hits / n_est,
        "mean_halfwidth": mean_hw,
        "needed_halfwidth": needed_hw,
        "narrowness_factor": needed_hw / mean_hw,
        "ok_rate": ok_days / n_est,
    }


@pytest.fixture(scope="session")
def coverage_by_window() -> dict[int, dict[str, float]]:
    """Sweep every window once per session (~2s total for all four)."""
    return {w: measure_coverage(w) for w in WINDOWS}


@pytest.mark.parametrize("window_days", WINDOWS)
def test_coverage_is_in_the_documented_band(coverage_by_window, window_days) -> None:
    """CHARACTERIZATION: coverage sits far below nominal, and stays where documented.

    Failing because coverage IMPROVED is a wanted outcome, not a false alarm — update
    the band and the module docstring table, then narrow the gap in
    ``test_reported_ci_achieves_nominal_coverage``.
    """
    result = coverage_by_window[window_days]
    lo, hi = COVERAGE_BAND
    assert lo <= result["coverage"] <= hi, (
        f"window={window_days}: empirical 95% CI coverage {result['coverage']:.3f} "
        f"left the documented band {COVERAGE_BAND}. Nominal is {NOMINAL_COVERAGE}. "
        f"If this is an improvement, update the band and the docstring table."
    )
    # And it is nowhere near nominal — the headline of this module.
    assert result["coverage"] < NOMINAL_COVERAGE


@pytest.mark.parametrize("window_days", WINDOWS)
def test_reported_interval_is_far_too_narrow(coverage_by_window, window_days) -> None:
    """The actionable number: how much wider the interval needed to be.

    A future autocorrelation-aware SE should move this toward 1.0. Newey-West alone
    buys only ~1.2-1.9x, so closing it fully needs window-composition sensitivity too.
    """
    result = coverage_by_window[window_days]
    assert result["narrowness_factor"] > MIN_NARROWNESS_FACTOR, (
        f"window={window_days}: reported +/-{result['mean_halfwidth']:.0f} kcal vs "
        f"+/-{result['needed_halfwidth']:.0f} needed for {NOMINAL_COVERAGE:.0%} "
        f"({result['narrowness_factor']:.1f}x). Below {MIN_NARROWNESS_FACTOR}x the "
        f"CI is materially healthier — update this test and the docstring table."
    )


@pytest.mark.parametrize("window_days", WINDOWS)
def test_status_flag_never_catches_this(coverage_by_window, window_days) -> None:
    """The ``low_confidence`` gate offers no protection in this scenario.

    ``low_conf_halfwidth_kcal`` is 500 kcal, but the reported half-width here is
    ~35-64 kcal, so the gate never trips — every single day is reported ``ok`` while
    the interval misses the truth ~3 times in 4. Documented so nobody assumes the
    status flag is a safety net against a miscalibrated CI.
    """
    result = coverage_by_window[window_days]
    assert result["ok_rate"] == 1.0, (
        f"window={window_days}: ok_rate {result['ok_rate']:.3f} — the status flag "
        f"started catching these. That is an improvement; update this test."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN GAP (PRD §7.3): the OLS standard error assumes independent residuals "
        "but the fit runs on an EWMA-smoothed, autocorrelated series, so the reported "
        "95% interval achieves only ~23% coverage. Fixing this needs an "
        "autocorrelation-aware SE AND a window-composition sensitivity term. When it "
        "passes, delete the xfail and keep the assertion."
    ),
)
def test_reported_ci_achieves_nominal_coverage(coverage_by_window) -> None:
    """The target. Expected to fail today; strict=True makes an XPASS turn CI red."""
    result = coverage_by_window[28]
    assert result["coverage"] >= 0.90, (
        f"empirical coverage {result['coverage']:.3f} vs nominal {NOMINAL_COVERAGE}"
    )


def _print_table() -> None:
    """Human-readable summary — run this module directly to see the numbers."""
    print(
        f"TDEE 95% CI coverage vs known truth (true TDEE={TRUE_TDEE:.0f}, flat by "
        f"construction; {N_SEEDS} seeds x {REPLAY_DAYS} days)"
    )
    print(
        f"{'window':>7}{'n_est':>8}{'coverage':>10}{'mean±CI':>10}"
        f"{'±CI for 95%':>13}{'too narrow':>12}{'ok rate':>9}"
    )
    for w in WINDOWS:
        r = measure_coverage(w)
        print(
            f"{w:>7}{int(r['n_estimates']):>8}{r['coverage']:>10.3f}"
            f"{r['mean_halfwidth']:>10.0f}{r['needed_halfwidth']:>13.0f}"
            f"{r['narrowness_factor']:>11.1f}x{r['ok_rate']:>9.2f}"
        )
    print(f"nominal coverage for a 95% interval: {NOMINAL_COVERAGE:.2f}")


if __name__ == "__main__":
    _print_table()
