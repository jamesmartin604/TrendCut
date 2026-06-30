"""Tests for the synthetic ground-truth generator (Phase 1).

These tests assert mathematical / statistical properties of the *generator only*
— the analytic trajectory, reproducibility, missing-data behaviour, and the
autocorrelation structure of the noise. They deliberately do **not** try to
recover the hidden TDEE; that is Phase 3's job (PRD §11.1), and putting it here
would make the tests circular.
"""

import numpy as np
import pytest

from estimator.synthetic import DEFAULT_ENERGY_DENSITY, simulate


def _lag1_autocorr(x: np.ndarray) -> float:
    """Sample lag-1 Pearson autocorrelation of a 1-D series."""
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


# --------------------------------------------------------------------------- #
# Zero-noise: observed trajectory equals the analytic energy-balance expectation
# --------------------------------------------------------------------------- #
def test_zero_noise_matches_analytic_constant_intake():
    n_days = 120
    tdee = 2600.0
    intake = 2100.0  # 500 kcal/day deficit
    density = 7700.0
    start = 82.0

    res = simulate(
        n_days,
        true_tdee=tdee,
        intake=intake,
        start_weight=start,
        true_energy_density=density,
        water_sd=0.0,
        spike_rate=0.0,
        missing_weight_rate=0.0,
        missing_intake_rate=0.0,
        seed=0,
    )

    daily_change = (intake - tdee) / density
    expected = start + daily_change * np.arange(n_days)

    # With no noise, observed weight is exactly the clean analytic line.
    assert np.allclose(res.observed.weight, expected, atol=1e-9)
    assert np.allclose(res.truth.true_weight, expected, atol=1e-9)
    # Day 0 is the start weight by construction.
    assert res.observed.weight[0] == pytest.approx(start)
    # No gaps were requested.
    assert not np.any(np.isnan(res.observed.weight))
    assert not np.any(np.isnan(res.observed.intake))


def test_zero_noise_matches_analytic_variable_intake():
    """A per-day intake series (e.g. weekly refeeds) still matches the cumulative sum."""
    n_days = 60
    tdee = 2400.0
    density = 8000.0  # intentionally not the default, to exercise the parameter
    start = 75.0
    rng = np.random.default_rng(7)
    intake = 1900.0 + 600.0 * (rng.random(n_days) < 0.2)  # ~20% refeed days at +600

    res = simulate(
        n_days,
        true_tdee=tdee,
        intake=intake,
        start_weight=start,
        true_energy_density=density,
        water_sd=0.0,
        spike_rate=0.0,
        seed=1,
    )

    weight_change = (intake - tdee) / density
    expected = start + np.concatenate(([0.0], np.cumsum(weight_change)[:-1]))
    assert np.allclose(res.observed.weight, expected, atol=1e-9)
    # The realised intake is preserved verbatim in the truth.
    assert np.array_equal(res.truth.intake, intake)


def test_energy_density_is_honoured_not_hardcoded():
    """Driving with a non-default density must change the trajectory accordingly."""
    common = dict(n_days=50, true_tdee=2500.0, intake=2000.0, water_sd=0.0, spike_rate=0.0, seed=3)
    default = simulate(**common, true_energy_density=DEFAULT_ENERGY_DENSITY)
    denser = simulate(**common, true_energy_density=9400.0)
    # Same deficit, larger kcal/kg -> smaller weight change -> less loss.
    assert abs(denser.truth.true_weight[-1] - 80.0) < abs(default.truth.true_weight[-1] - 80.0)


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #
def test_same_seed_identical_output():
    kw = dict(n_days=200, water_sd=0.6, spike_rate=0.05, missing_weight_rate=0.1, seed=42)
    a = simulate(**kw)
    b = simulate(**kw)
    assert np.array_equal(a.observed.weight, b.observed.weight, equal_nan=True)
    assert np.array_equal(a.observed.intake, b.observed.intake, equal_nan=True)
    assert np.array_equal(a.truth.true_weight, b.truth.true_weight)


def test_different_seed_different_output():
    kw = dict(n_days=200, water_sd=0.6, spike_rate=0.05, missing_weight_rate=0.1)
    a = simulate(**kw, seed=1)
    b = simulate(**kw, seed=2)
    # The clean truth is identical (noise-free), but the noisy observation differs.
    assert np.array_equal(a.truth.true_weight, b.truth.true_weight)
    assert not np.array_equal(a.observed.weight, b.observed.weight, equal_nan=True)


# --------------------------------------------------------------------------- #
# Missing-data rates come out approximately as configured
# --------------------------------------------------------------------------- #
def test_missing_rates_approximately_match():
    n_days = 20000
    w_rate, i_rate = 0.30, 0.15
    res = simulate(
        n_days,
        water_sd=0.5,
        spike_rate=0.03,
        missing_weight_rate=w_rate,
        missing_intake_rate=i_rate,
        seed=2024,
    )
    frac_w = np.isnan(res.observed.weight).mean()
    frac_i = np.isnan(res.observed.intake).mean()
    assert frac_w == pytest.approx(w_rate, abs=0.02)
    assert frac_i == pytest.approx(i_rate, abs=0.02)


def test_missing_masks_are_independent():
    """Dropping weight and dropping intake should be independent events."""
    n_days = 20000
    res = simulate(
        n_days,
        missing_weight_rate=0.3,
        missing_intake_rate=0.3,
        water_sd=0.0,
        spike_rate=0.0,
        seed=99,
    )
    w_missing = np.isnan(res.observed.weight)
    i_missing = np.isnan(res.observed.intake)
    joint = (w_missing & i_missing).mean()
    # Under independence P(both) ~ 0.3 * 0.3 = 0.09.
    assert joint == pytest.approx(0.09, abs=0.02)


def test_no_missing_when_rates_zero():
    res = simulate(100, water_sd=0.4, spike_rate=0.05, seed=5)
    assert not np.any(np.isnan(res.observed.weight))
    assert not np.any(np.isnan(res.observed.intake))


# --------------------------------------------------------------------------- #
# Noise is autocorrelated (AR(1)), not i.i.d.
# --------------------------------------------------------------------------- #
def test_water_noise_is_autocorrelated():
    n_days = 4000
    phi = 0.7
    res = simulate(
        n_days,
        intake=2500.0,
        true_tdee=2500.0,  # flat truth so noise == observed - true cleanly
        water_sd=0.5,
        water_autocorr=phi,
        spike_rate=0.0,  # isolate the AR(1) water component
        missing_weight_rate=0.0,
        seed=11,
    )
    noise = res.observed.weight - res.truth.true_weight
    lag1 = _lag1_autocorr(noise)
    # Clearly positive and in the neighbourhood of the configured phi.
    assert lag1 > 0.4
    assert lag1 == pytest.approx(phi, abs=0.1)


def test_iid_noise_has_near_zero_autocorrelation():
    """With autocorr=0 the same machinery must produce (near) uncorrelated noise."""
    n_days = 4000
    res = simulate(
        n_days,
        intake=2500.0,
        true_tdee=2500.0,
        water_sd=0.5,
        water_autocorr=0.0,
        spike_rate=0.0,
        seed=12,
    )
    noise = res.observed.weight - res.truth.true_weight
    assert abs(_lag1_autocorr(noise)) < 0.1


def test_water_noise_has_roughly_configured_magnitude():
    n_days = 5000
    sd = 0.8
    res = simulate(
        n_days,
        intake=2500.0,
        true_tdee=2500.0,
        water_sd=sd,
        water_autocorr=0.6,
        spike_rate=0.0,
        seed=13,
    )
    noise = res.observed.weight - res.truth.true_weight
    assert noise.std() == pytest.approx(sd, abs=0.1)


# --------------------------------------------------------------------------- #
# A steady deficit loses weight in the expected direction and magnitude
# --------------------------------------------------------------------------- #
def test_steady_deficit_loses_weight():
    n_days = 90
    tdee = 2500.0
    deficit = 500.0
    density = 7700.0
    res = simulate(
        n_days,
        true_tdee=tdee,
        intake=tdee - deficit,
        start_weight=85.0,
        true_energy_density=density,
        water_sd=0.5,
        water_autocorr=0.7,
        spike_rate=0.03,
        seed=7,
    )

    # Direction: the clean trajectory goes down.
    assert res.truth.true_weight[-1] < res.truth.true_weight[0]

    # Magnitude: total loss ~ n * deficit / density (last day has n-1 changes applied).
    expected_loss = (n_days - 1) * deficit / density
    actual_loss = res.truth.true_weight[0] - res.truth.true_weight[-1]
    assert actual_loss == pytest.approx(expected_loss, rel=1e-9)

    # Even through noise, an early-window mean is heavier than a late-window mean.
    early = np.nanmean(res.observed.weight[:14])
    late = np.nanmean(res.observed.weight[-14:])
    assert late < early


def test_surplus_gains_weight():
    res = simulate(
        60,
        true_tdee=2200.0,
        intake=2700.0,  # eating above maintenance
        start_weight=70.0,
        water_sd=0.0,
        spike_rate=0.0,
        seed=8,
    )
    assert res.truth.true_weight[-1] > res.truth.true_weight[0]


# --------------------------------------------------------------------------- #
# API surface / validation
# --------------------------------------------------------------------------- #
def test_intake_callable_is_supported():
    res = simulate(
        30,
        intake=lambda d: 2000.0 + (300.0 if d % 7 == 0 else 0.0),  # weekly refeed
        water_sd=0.0,
        spike_rate=0.0,
        seed=0,
    )
    assert res.truth.intake[0] == pytest.approx(2300.0)
    assert res.truth.intake[1] == pytest.approx(2000.0)


def test_dates_populated_when_start_date_given():
    import datetime

    res = simulate(5, start_date=datetime.date(2026, 1, 1), seed=0)
    assert res.observed.dates is not None
    assert res.observed.dates[0] == np.datetime64("2026-01-01")
    assert res.observed.dates[-1] == np.datetime64("2026-01-05")


def test_dates_none_by_default():
    res = simulate(5, seed=0)
    assert res.observed.dates is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_days": 0},
        {"n_days": 10, "true_energy_density": 0.0},
        {"n_days": 10, "water_autocorr": 1.0},
        {"n_days": 10, "spike_rate": 1.5},
        {"n_days": 10, "intake": [1.0, 2.0]},  # wrong length
    ],
)
def test_invalid_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        simulate(**kwargs)
