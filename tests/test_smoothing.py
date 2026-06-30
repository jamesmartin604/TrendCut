"""Tests for the gap-aware EWMA trend smoother (Phase 2).

Covers the hand-computed recurrence, convergence, lag/responsiveness, the
critical gap-awareness property (trend depends on elapsed time, not array
position), real denoising against the synthetic simulator, and the documented
edge cases (single point, leading gaps, all-missing).
"""

import numpy as np
import pytest

from estimator.smoothing import DEFAULT_ALPHA, effective_window, ewma_trend
from estimator.synthetic import simulate


# --------------------------------------------------------------------------- #
# Hand-computed small case (no gaps)
# --------------------------------------------------------------------------- #
def test_hand_computed_recurrence():
    # alpha = 0.5, trend[t] = 0.5*trend[t-1] + 0.5*weight[t], seeded at weight[0].
    weights = [80.0, 81.0, 79.0, 82.0]
    #   t0 = 80
    #   t1 = 0.5*80    + 0.5*81 = 80.5
    #   t2 = 0.5*80.5  + 0.5*79 = 79.75
    #   t3 = 0.5*79.75 + 0.5*82 = 80.875
    expected = [80.0, 80.5, 79.75, 80.875]
    assert np.allclose(ewma_trend(weights, alpha=0.5), expected, atol=1e-12)


def test_output_length_matches_input():
    out = ewma_trend([80.0, 81.0, 82.0, 83.0], alpha=0.3)
    assert out.shape == (4,)


def test_alpha_one_disables_smoothing():
    weights = [80.0, 85.0, 78.0]
    assert np.allclose(ewma_trend(weights, alpha=1.0), weights, atol=1e-12)


# --------------------------------------------------------------------------- #
# Convergence
# --------------------------------------------------------------------------- #
def test_constant_series_converges_to_constant():
    out = ewma_trend([70.0] * 50, alpha=DEFAULT_ALPHA)
    assert np.allclose(out, 70.0, atol=1e-12)


def test_converges_toward_new_constant_level():
    # Long run at a new level: the trend approaches it arbitrarily closely.
    out = ewma_trend([80.0] + [90.0] * 300, alpha=0.1)
    assert out[-1] == pytest.approx(90.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Lag / responsiveness
# --------------------------------------------------------------------------- #
def test_step_is_tracked_but_lags_raw():
    alpha = 0.2
    weights = [80.0] * 5 + [90.0] * 15
    out = ewma_trend(weights, alpha=alpha)
    post = out[5:]  # trend after the step
    # Tracks toward the new level: strictly increasing and bounded in (80, 90).
    assert np.all(np.diff(post) > 0)
    assert np.all(post > 80.0)
    assert np.all(post < 90.0)
    # But it does eventually get close.
    assert out[-1] == pytest.approx(90.0, abs=0.5)


def test_smaller_alpha_lags_more():
    weights = [80.0] * 5 + [90.0] * 15
    slow = ewma_trend(weights, alpha=0.05)
    fast = ewma_trend(weights, alpha=0.40)
    # At every day after the step, the smaller alpha sits closer to the old level.
    for i in range(5, len(weights)):
        assert slow[i] < fast[i]


# --------------------------------------------------------------------------- #
# Gap-awareness: weighting depends on elapsed time, not array position
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("k", [1, 2, 3, 5, 9])
def test_k_day_gap_applies_one_minus_alpha_to_the_k(k):
    alpha = 0.1
    series = [10.0] + [np.nan] * (k - 1) + [20.0]  # obs at day 0 and day k
    out = ewma_trend(series, alpha=alpha)
    decay = (1.0 - alpha) ** k
    expected = decay * 10.0 + (1.0 - decay) * 20.0
    assert out[-1] == pytest.approx(expected, abs=1e-12)


def test_gap_equals_repeating_next_value_over_the_gap():
    """A k-day gap with new value w == observing w on every intervening day.

    This is the position-independence property: the sparse series (with NaNs) and
    the dense series (gap filled by repeating the next real value) reach the same
    trend at the comparison day.
    """
    alpha = 0.1
    sparse = [10.0, np.nan, np.nan, 20.0]  # 3-day gap
    dense = [10.0, 20.0, 20.0, 20.0]  # same value observed each of the 3 days
    assert ewma_trend(sparse, alpha=alpha)[-1] == pytest.approx(
        ewma_trend(dense, alpha=alpha)[-1], abs=1e-12
    )


def test_missing_days_carry_trend_forward():
    alpha = 0.1
    out = ewma_trend([80.0, np.nan, np.nan, 90.0], alpha=alpha)
    # The gap days hold the last trend flat (seeded at 80).
    assert out[0] == pytest.approx(80.0)
    assert out[1] == pytest.approx(80.0)
    assert out[2] == pytest.approx(80.0)
    # The next real obs decays against the 3-day elapsed gap.
    decay = (1.0 - alpha) ** 3
    assert out[3] == pytest.approx(decay * 80.0 + (1.0 - decay) * 90.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# Real denoising: integration with the synthetic simulator
# --------------------------------------------------------------------------- #
def test_smoothing_reduces_variance_vs_raw():
    res = simulate(
        n_days=200,
        true_tdee=2600,
        intake=2100,
        start_weight=85.0,
        water_sd=0.5,
        water_autocorr=0.7,
        spike_rate=0.05,
        missing_weight_rate=0.1,
        seed=2024,
    )
    observed = res.observed.weight
    truth = res.truth.true_weight
    trend = ewma_trend(observed, alpha=DEFAULT_ALPHA)

    # Compare residuals on days that were actually logged (raw is present there).
    logged = ~np.isnan(observed)
    raw_resid = observed[logged] - truth[logged]
    smooth_resid = trend[logged] - truth[logged]

    # Variance is the right metric: the EWMA's lag adds a near-constant offset on
    # a sloped truth (shifts the mean, not the spread), so variance isolates the
    # denoising. It should drop substantially.
    assert smooth_resid.var() < 0.5 * raw_resid.var()


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_single_observation():
    assert np.allclose(ewma_trend([75.0], alpha=0.1), [75.0])


def test_leading_gaps_are_nan_until_first_observation():
    out = ewma_trend([np.nan, np.nan, 80.0, 81.0], alpha=0.5)
    assert np.isnan(out[0])
    assert np.isnan(out[1])
    assert out[2] == pytest.approx(80.0)  # seeded with first real value
    assert out[3] == pytest.approx(80.5)  # 0.5*80 + 0.5*81


def test_all_missing_input_is_all_nan():
    out = ewma_trend([np.nan, np.nan, np.nan], alpha=0.1)
    assert out.shape == (3,)
    assert np.all(np.isnan(out))


def test_none_is_treated_as_missing():
    # None should behave exactly like NaN (the simulator may emit either).
    out = ewma_trend([80.0, None, 90.0], alpha=0.1)
    assert out[1] == pytest.approx(80.0)  # carried forward
    decay = (1.0 - 0.1) ** 2
    assert out[2] == pytest.approx(decay * 80.0 + (1.0 - decay) * 90.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# alpha <-> effective window, and validation
# --------------------------------------------------------------------------- #
def test_effective_window_center_of_mass():
    assert effective_window(0.1) == pytest.approx(9.0)
    assert effective_window(0.5) == pytest.approx(1.0)
    assert effective_window(1.0) == pytest.approx(0.0)


@pytest.mark.parametrize("alpha", [0.0, -0.1, 1.5])
def test_invalid_alpha_raises(alpha):
    with pytest.raises(ValueError):
        ewma_trend([80.0, 81.0], alpha=alpha)
    with pytest.raises(ValueError):
        effective_window(alpha)


def test_two_dimensional_input_raises():
    with pytest.raises(ValueError):
        ewma_trend([[80.0, 81.0], [82.0, 83.0]], alpha=0.1)
