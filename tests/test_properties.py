"""Property-based tests (Phase 4) for the estimator and smoother.

These assert *invariants* that must hold for EVERY valid input, rather than
specific numeric answers (PRD §11.2). `hypothesis` generates realistic-but-
adversarial daily series — plausible weights and intakes, arbitrary missing-data
patterns (including heavy sparsity), noise + spikes, and edge lengths — and each
test states a property the estimator must never violate.

Invariants covered:
  1. Boundedness            — an "ok" estimate is finite and physiologically sane.
  2. Graceful insufficiency — every input yields ok / low_confidence /
                              insufficient_data and NEVER raises (no third outcome).
  3. Stability / continuity — one extra ordinary day moves the estimate by a
                              bounded amount (no wild oscillation).
  4. Monotonic sanity       — shifting all intake by X shifts TDEE by exactly X and
                              leaves the implied deficit unchanged.
  5. Idempotence            — identical input -> identical output (pure function).
  6. CI ordering            — ci_low <= tdee <= ci_high whenever a CI exists.
  7. Smoothing boundedness  — the EWMA trend never leaves the [min, max] range of
                              the observed weights it is built from.

Hypothesis profiles (registered below):
  * "ci"   (default) — 150 examples/test, no deadline: stable and quick for CI.
  * "deep"           — 2500 examples/test for heavier local fuzzing.
Select the deep profile with the HYPOTHESIS_PROFILE env var (see the module
README / test docstring at the bottom for the exact command).
"""

import math
import os

import numpy as np
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from estimator.smoothing import ewma_trend
from estimator.tdee import PLAUSIBLE_TDEE, estimate_tdee

# --------------------------------------------------------------------------- #
# Hypothesis profiles
# --------------------------------------------------------------------------- #
_COMMON = {
    "deadline": None,  # the EWMA loop is O(n); avoid deadline flakes in CI
    "suppress_health_check": [HealthCheck.filter_too_much, HealthCheck.too_slow],
}
settings.register_profile("ci", max_examples=150, **_COMMON)
settings.register_profile("deep", max_examples=2500, **_COMMON)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

# Documented bound for invariant 3 (see module header): appending one ordinary day
# to a window-28 "ok" estimate moved TDEE by median ~11, p99 ~47, max ~71 kcal over
# 3000 random trials. The assertion bound is set well above that to flag genuine
# oscillation while never flaking on ordinary continuations.
STABILITY_BOUND_KCAL = 200.0

_FLOAT = {"allow_nan": False, "allow_infinity": False}


def _unit_noise(draw, n: int) -> np.ndarray:
    """A length-n array of independent values in [-1, 1]."""
    return np.array(draw(st.lists(st.floats(-1.0, 1.0, **_FLOAT), min_size=n, max_size=n)))


@st.composite
def _series(draw, *, min_len: int = 1, max_len: int = 90):
    """A realistic-but-adversarial (weights, intake) pair of equal length.

    Weights sit in a human range with a drift, autocorrelation-free noise, and
    occasional upward spikes; intake sits in a plausible kcal range. Both get
    independent, arbitrarily-dense missing-data masks (NaN).
    """
    n = draw(st.integers(min_len, max_len))
    day = np.arange(n)

    base = draw(st.floats(45.0, 160.0))
    drift = draw(st.floats(-0.3, 0.3))
    noise_sd = draw(st.floats(0.0, 2.5))
    spike_mag = draw(st.floats(0.0, 4.0))
    spikes = np.where(_unit_noise(draw, n) > 0.6, spike_mag, 0.0)
    weight = base + drift * day + noise_sd * _unit_noise(draw, n) + spikes
    weight = np.clip(weight, 40.0, 250.0)

    w_miss_rate = draw(st.floats(0.0, 0.6))
    weight = np.where(np.abs(_unit_noise(draw, n)) < w_miss_rate, np.nan, weight)

    intake_base = draw(st.floats(800.0, 4500.0))
    intake = intake_base + 600.0 * _unit_noise(draw, n)
    intake = np.clip(intake, 0.0, 8000.0)
    i_miss_rate = draw(st.floats(0.0, 0.6))
    intake = np.where(np.abs(_unit_noise(draw, n)) < i_miss_rate, np.nan, intake)

    return weight, intake


@st.composite
def _wellbehaved_series(draw):
    """A dense, moderate-noise cut that reliably yields an "ok" estimate.

    Used for the stability invariant, where we need two comparable "ok" estimates.
    """
    n = draw(st.integers(40, 70))
    day = np.arange(n)
    base = draw(st.floats(60.0, 110.0))
    drift = draw(st.floats(-0.12, 0.05))
    noise_sd = draw(st.floats(0.1, 0.8))
    weight = base + drift * day + noise_sd * _unit_noise(draw, n)

    intake_base = draw(st.floats(1400.0, 2600.0))
    intake = np.clip(intake_base + 250.0 * _unit_noise(draw, n), 0.0, 6000.0)
    return weight, intake


_WINDOW = st.integers(5, 40)
_ALPHA = st.floats(0.01, 1.0, **_FLOAT)


# --------------------------------------------------------------------------- #
# 2 + 6: never raises, status trichotomy, CI ordering
# --------------------------------------------------------------------------- #
@given(_series(), _WINDOW, _ALPHA)
def test_status_trichotomy_and_never_raises(data, window, alpha):
    weight, intake = data
    est = estimate_tdee(weight, intake, window_days=window, alpha=alpha)

    assert est.status in {"ok", "low_confidence", "insufficient_data"}
    if est.status == "insufficient_data":
        assert est.tdee is None
        assert est.ci_low is None and est.ci_high is None
    else:
        # A number was produced: it must be finite and its CI correctly ordered.
        assert est.tdee is not None
        assert math.isfinite(est.tdee)
        assert math.isfinite(est.ci_low) and math.isfinite(est.ci_high)
        assert est.ci_low <= est.tdee <= est.ci_high  # invariant 6


# --------------------------------------------------------------------------- #
# 1: boundedness of an "ok" estimate
# --------------------------------------------------------------------------- #
@given(_series(), _WINDOW, _ALPHA)
def test_ok_estimate_is_physiologically_bounded(data, window, alpha):
    weight, intake = data
    est = estimate_tdee(weight, intake, window_days=window, alpha=alpha)
    assume(est.status == "ok")

    lo, hi = PLAUSIBLE_TDEE
    assert lo <= est.tdee <= hi
    assert 0.0 < est.ci_low <= est.ci_high < 8000.0
    assert math.isfinite(est.slope_kg_per_day) and abs(est.slope_kg_per_day) < 5.0
    assert math.isfinite(est.slope_se) and est.slope_se >= 0.0
    assert math.isfinite(est.mean_intake)


# --------------------------------------------------------------------------- #
# 5: idempotence / determinism
# --------------------------------------------------------------------------- #
@given(_series(), _WINDOW, _ALPHA)
def test_idempotent(data, window, alpha):
    weight, intake = data
    a = estimate_tdee(weight, intake, window_days=window, alpha=alpha)
    b = estimate_tdee(weight, intake, window_days=window, alpha=alpha)
    assert a == b  # frozen dataclass of scalars/None — exact equality


# --------------------------------------------------------------------------- #
# 4: monotonic sanity — shifting all intake by X shifts TDEE by exactly X
# --------------------------------------------------------------------------- #
@given(_series(), _WINDOW, _ALPHA, st.floats(-500.0, 500.0, **_FLOAT))
def test_intake_shift_moves_tdee_by_exactly_x(data, window, alpha, x):
    weight, intake = data
    base = estimate_tdee(weight, intake, window_days=window, alpha=alpha)
    assume(base.status != "insufficient_data")  # need a number to compare

    # Adding X to every day keeps the missing mask (NaN + X == NaN) and the slope
    # (weights unchanged), so the intake mean — and hence TDEE — shifts by exactly X.
    shifted = estimate_tdee(weight, intake + x, window_days=window, alpha=alpha)
    assert shifted.tdee == pytest.approx(base.tdee + x, abs=1e-6)
    # The implied deficit (mean_intake - tdee) is invariant to the intake level.
    assert (shifted.mean_intake - shifted.tdee) == pytest.approx(
        base.mean_intake - base.tdee, abs=1e-6
    )


# --------------------------------------------------------------------------- #
# 3: stability — one extra ordinary day cannot swing the estimate wildly
# --------------------------------------------------------------------------- #
@given(_wellbehaved_series(), st.floats(-1.0, 1.0, **_FLOAT))
def test_appending_one_ordinary_day_is_stable(data, extra_unit):
    weight, intake = data
    window = 28
    base = estimate_tdee(weight, intake, window_days=window)
    assume(base.status == "ok")

    # An "ordinary" next day: weight near the last real value, typical intake.
    next_weight = weight[-1] + 0.8 * extra_unit
    next_intake = float(np.clip(np.nanmean(intake), 0.0, 6000.0))
    weight2 = np.append(weight, next_weight)
    intake2 = np.append(intake, next_intake)

    after = estimate_tdee(weight2, intake2, window_days=window)
    assume(after.status == "ok")
    assert abs(after.tdee - base.tdee) < STABILITY_BOUND_KCAL


# --------------------------------------------------------------------------- #
# 7: smoothing trend stays within the observed weight range
# --------------------------------------------------------------------------- #
@given(_series(), _ALPHA)
def test_ewma_trend_within_observed_range(data, alpha):
    weight, _ = data
    trend = ewma_trend(weight, alpha=alpha)

    observed = weight[~np.isnan(weight)]
    if observed.size == 0:
        assert np.all(np.isnan(trend))  # all-missing -> all-NaN
        return

    lo, hi = float(observed.min()), float(observed.max())
    trend_defined = trend[~np.isnan(trend)]
    # The EWMA is a convex combination of observed values, so it can never exceed
    # their range (tiny epsilon for float rounding only).
    assert np.all(trend_defined >= lo - 1e-9)
    assert np.all(trend_defined <= hi + 1e-9)


# To fuzz harder locally (PowerShell):
#     $env:HYPOTHESIS_PROFILE="deep"; .venv\Scripts\python.exe -m pytest tests/test_properties.py -q
#     Remove-Item Env:HYPOTHESIS_PROFILE
