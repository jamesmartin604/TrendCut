"""Tests for the TDEE estimator (Phase 3) — concrete, fixed-seed only.

No hypothesis / property-based tests here; those are Phase 4. Every test below
uses explicit parameters and fixed seeds so the recovered-vs-true numbers are
reproducible and auditable.

The headline test (``test_headline_recovery_matched_density``) is the project's
go/no-go signal: across a matrix of true TDEEs and deficits under moderate noise,
with the simulator's ``true_energy_density`` equal to the estimator's
``assumed_energy_density``, the recovered TDEE must land within +/-100 kcal.

Measured behaviour (see commit notes), so the tolerances are honest, not loose:
* headline (window=28, matched, water_sd=0.4): max abs error ~70 kcal over 45 cases.
* robust (window=42, water_sd=0.6 + spikes + missing): max abs error ~64 kcal.
* density mismatch: bias tracks ``deficit*(assumed-true)/true`` to within a few kcal.
"""

import numpy as np
import pytest

from estimator.synthetic import simulate
from estimator.tdee import DEFAULT_ASSUMED_ENERGY_DENSITY, estimate_tdee


def _recover(
    true_tdee, deficit, *, window, seed, water_sd, autocorr, spike, miss, true_density=7700.0
):
    """Simulate a cut and return (estimate, true_tdee)."""
    n_days = window + 40  # ample EWMA warm-up before the regression window
    res = simulate(
        n_days=n_days,
        true_tdee=true_tdee,
        intake=true_tdee - deficit,
        start_weight=85.0,
        true_energy_density=true_density,
        water_sd=water_sd,
        water_autocorr=autocorr,
        spike_rate=spike,
        missing_weight_rate=miss,
        missing_intake_rate=0.0,
        seed=seed,
    )
    est = estimate_tdee(res.observed.weight, res.observed.intake, window_days=window)
    return est, true_tdee


# --------------------------------------------------------------------------- #
# HEADLINE recovery — the go/no-go test
# --------------------------------------------------------------------------- #
def test_headline_recovery_matched_density():
    """Matched density, moderate noise: recover known TDEE within +/-100 kcal."""
    errors = []
    for true_tdee in (2200, 2500, 2800):
        for deficit in (300, 500, 750):
            for seed in range(5):
                est, tt = _recover(
                    true_tdee,
                    deficit,
                    window=28,
                    seed=seed,
                    water_sd=0.4,
                    autocorr=0.6,
                    spike=0.0,
                    miss=0.0,
                )
                assert est.status == "ok"
                assert est.tdee is not None
                errors.append(est.tdee - tt)
    errors = np.array(errors)
    assert np.abs(errors).max() < 100.0, f"max abs error {np.abs(errors).max():.1f} kcal"


def test_robustness_recovery_with_spikes_and_gaps():
    """Autocorrelated noise + spikes + missing days: recover within a wider +/-150 kcal."""
    errors = []
    for true_tdee in (2200, 2500, 2800):
        for deficit in (300, 500, 750):
            for seed in range(5):
                est, tt = _recover(
                    true_tdee,
                    deficit,
                    window=42,
                    seed=seed,
                    water_sd=0.6,
                    autocorr=0.7,
                    spike=0.05,
                    miss=0.1,
                )
                assert est.tdee is not None
                errors.append(est.tdee - tt)
    errors = np.array(errors)
    assert np.abs(errors).max() < 150.0, f"max abs error {np.abs(errors).max():.1f} kcal"


# --------------------------------------------------------------------------- #
# Density mismatch is physics, not a bug — pin down the bias
# --------------------------------------------------------------------------- #
def test_density_mismatch_biases_as_expected():
    """When assumed != true density, bias ~= deficit*(assumed-true)/true, both signs."""
    deficit = 600
    assumed = DEFAULT_ASSUMED_ENERGY_DENSITY  # 7700

    for true_density in (7000.0, 8400.0):
        expected_bias = deficit * (assumed - true_density) / true_density
        biases = []
        for seed in range(10):
            est, tt = _recover(
                2500,
                deficit,
                window=42,
                seed=seed,
                water_sd=0.2,
                autocorr=0.5,
                spike=0.0,
                miss=0.0,
                true_density=true_density,
            )
            biases.append(est.tdee - tt)
        biases = np.array(biases)
        # Mean bias matches the closed form, and every run is on the correct side.
        assert biases.mean() == pytest.approx(expected_bias, abs=20.0)
        assert np.all(np.sign(biases) == np.sign(expected_bias))


# --------------------------------------------------------------------------- #
# Sign sanity
# --------------------------------------------------------------------------- #
def test_steady_deficit_puts_tdee_above_intake():
    est, _ = _recover(2500, 500, window=28, seed=0, water_sd=0.3, autocorr=0.5, spike=0.0, miss=0.0)
    assert est.slope_kg_per_day < 0  # losing weight
    assert est.tdee > est.mean_intake


def test_surplus_puts_tdee_below_intake():
    est, _ = _recover(
        2500, -500, window=28, seed=0, water_sd=0.3, autocorr=0.5, spike=0.0, miss=0.0
    )
    assert est.slope_kg_per_day > 0  # gaining weight
    assert est.tdee < est.mean_intake


# --------------------------------------------------------------------------- #
# Window behaviour — regression uses ONLY the last N days
# --------------------------------------------------------------------------- #
def test_window_isolation_is_exact_without_smoothing():
    """With alpha=1 (no smoothing memory), data before the window cannot change the estimate."""
    res = simulate(n_days=96, true_tdee=2500, intake=2000, start_weight=85.0, water_sd=0.3, seed=1)
    w = res.observed.weight.copy()
    cal = res.observed.intake.copy()
    base = estimate_tdee(w, cal, window_days=28, alpha=1.0)

    w2, cal2 = w.copy(), cal.copy()
    w2[:60] = 200.0  # absurd values, but all strictly before the 28-day window
    cal2[:60] = 9999.0
    changed = estimate_tdee(w2, cal2, window_days=28, alpha=1.0)

    assert changed.tdee == base.tdee
    assert changed.slope_kg_per_day == base.slope_kg_per_day
    assert changed.ci_low == base.ci_low and changed.ci_high == base.ci_high


# --------------------------------------------------------------------------- #
# Graceful insufficiency / low confidence — never raise, classify instead
# --------------------------------------------------------------------------- #
def test_all_missing_weight_is_insufficient():
    n = 30
    est = estimate_tdee(np.full(n, np.nan), np.full(n, 2000.0), window_days=14)
    assert est.status == "insufficient_data"
    assert est.tdee is None
    assert est.n_valid_weight_days == 0


def test_too_few_intake_days_is_insufficient():
    n = 30
    weights = 80.0 - 0.05 * np.arange(n)
    cal = np.full(n, np.nan)
    cal[:3] = 2000.0  # only 3 logged intake days
    est = estimate_tdee(weights, cal, window_days=14)
    assert est.status == "insufficient_data"
    assert est.tdee is None


def test_single_weighin_is_insufficient_not_a_crash():
    """A single weigh-in in the window has near-zero time variance — classify, don't raise."""
    n = 30
    weights = np.full(n, np.nan)
    weights[25] = 80.0
    est = estimate_tdee(weights, np.full(n, 2000.0), window_days=14)
    assert est.status == "insufficient_data"
    assert est.n_valid_weight_days == 1


def test_constant_weight_does_not_crash():
    """Zero weight variance: slope 0, tight CI — a valid (if uninformative) 'ok'."""
    n = 30
    est = estimate_tdee(np.full(n, 80.0), np.full(n, 2000.0), window_days=14)
    assert est.status == "ok"
    assert est.slope_kg_per_day == pytest.approx(0.0)
    assert est.tdee == pytest.approx(2000.0)


def test_implausible_estimate_is_low_confidence():
    """A physically extreme implied TDEE (outside 800-6000) is flagged, not trusted."""
    day = np.arange(30)
    weights = 90.0 - 0.8 * day  # crashing 0.8 kg/day
    est = estimate_tdee(weights, np.full(30, 2500.0), window_days=28)
    assert est.status == "low_confidence"
    assert est.tdee is not None  # the number is still returned, just flagged


def test_wide_ci_is_low_confidence():
    """Genuinely large slope SE (no smoothing, high noise, short window) -> low_confidence."""
    rng = np.random.default_rng(0)
    weights = 80.0 - 0.05 * np.arange(20) + rng.normal(0, 2.5, 20)
    est = estimate_tdee(weights, np.full(20, 2000.0), window_days=14, alpha=1.0)
    assert est.status == "low_confidence"
    assert (est.ci_high - est.tdee) > 500.0


# --------------------------------------------------------------------------- #
# Result shape and input validation
# --------------------------------------------------------------------------- #
def test_ok_estimate_populates_all_fields():
    est, _ = _recover(2500, 500, window=28, seed=2, water_sd=0.3, autocorr=0.5, spike=0.0, miss=0.0)
    assert est.status == "ok"
    for value in (
        est.tdee,
        est.ci_low,
        est.ci_high,
        est.slope_kg_per_day,
        est.slope_se,
        est.mean_intake,
    ):
        assert value is not None
    assert est.ci_low < est.tdee < est.ci_high
    assert est.n_valid_weight_days == 28
    assert est.n_valid_intake_days == 28
    assert est.assumed_energy_density == DEFAULT_ASSUMED_ENERGY_DENSITY


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window_days": 0},
        {"alpha": 0.0},
        {"alpha": 1.5},
        {"assumed_energy_density": 0.0},
    ],
)
def test_invalid_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        estimate_tdee(np.full(20, 80.0), np.full(20, 2000.0), **kwargs)


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        estimate_tdee(np.full(20, 80.0), np.full(19, 2000.0))
