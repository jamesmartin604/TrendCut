"""Tests for the graded plateau classifier (Phase 5).

Curated, human-labelled fixtures. Each test encodes a scenario whose correct
verdict a human can reason about, then asserts the classifier agrees. The
labels — not just "code matches label" — are the thing under review here; the
docstring on each test says *why* it has the label it does.

The pivotal one is ``test_swing_is_not_flagged_as_stalled``: a rising *raw* scale
that must NOT be called a plateau because the *smoothed* trend is still falling.

Realistic fixtures come from the simulator; boundary/degenerate fixtures are
hand-built ``TDEEEstimate`` objects so slope and slope_se can be pinned exactly.
"""

import numpy as np
import pytest

from estimator.plateau import (
    DEFAULT_DEAD_ZONE_KG_PER_DAY,
    classify_plateau,
)
from estimator.synthetic import simulate
from estimator.tdee import TDEEEstimate, estimate_tdee

DENSITY = 7700.0


def _make_estimate(slope, slope_se, *, status="ok", mean_intake=2100.0, density=DENSITY, window=28):
    """Build a TDEEEstimate directly, to pin slope/slope_se for boundary tests."""
    if slope is None:
        tdee = ci_low = ci_high = None
    else:
        tdee = mean_intake - slope * density
        half = 1.96 * slope_se * density
        ci_low, ci_high = tdee - half, tdee + half
    return TDEEEstimate(
        tdee=tdee,
        ci_low=ci_low,
        ci_high=ci_high,
        slope_kg_per_day=slope,
        slope_se=slope_se,
        mean_intake=mean_intake,
        window_days=window,
        n_valid_weight_days=window,
        n_valid_intake_days=window,
        assumed_energy_density=density,
        status=status,
    )


# --------------------------------------------------------------------------- #
# Realistic labelled fixtures (via the simulator)
# --------------------------------------------------------------------------- #
def test_clear_loss_is_on_track():
    """Real 500 kcal deficit -> steadily falling trend -> on_track.

    Label rationale: the person eats 2100 while truly burning 2600, so weight
    genuinely drops; the smoothed slope is clearly negative and the intended
    deficit is being realised.
    """
    res = simulate(
        n_days=68,
        true_tdee=2600,
        intake=2100,
        start_weight=85.0,
        water_sd=0.4,
        water_autocorr=0.6,
        seed=1,
    )
    est = estimate_tdee(res.observed.weight, res.observed.intake, window_days=28)
    verdict = classify_plateau(est, expected_deficit_kcal=500)
    assert verdict.state == "on_track"


def test_clear_plateau_is_stalled():
    """Adaptive thermogenesis: real maintenance == intake -> flat trend, yet the
    user still intends a 500 kcal deficit -> stalled.

    Label rationale: true TDEE has fallen to 2100 (what they eat), so the scale
    trend is flat despite them believing they're in a 500 kcal deficit. That is
    exactly the plateau the app exists to surface (FR-17).
    """
    res = simulate(
        n_days=68,
        true_tdee=2100,
        intake=2100,
        start_weight=85.0,
        water_sd=0.4,
        water_autocorr=0.6,
        seed=1,
    )
    est = estimate_tdee(res.observed.weight, res.observed.intake, window_days=28)
    verdict = classify_plateau(est, expected_deficit_kcal=500)

    assert verdict.state == "stalled"
    # The explanation must name the flat-trend-despite-deficit situation (FR-17).
    lower = verdict.explanation.lower()
    assert "deficit" in lower
    assert "flat" in lower or "adaptive" in lower


def test_swing_is_not_flagged_as_stalled():
    """THE false-positive guard: a rising RAW scale (refeed water) whose SMOOTHED
    trend is still falling must NOT be called a plateau.

    Label rationale: the underlying cut is real (true 500 deficit), but a refeed
    dumps ~1.6 kg of water onto the last few days, so the raw scale rises at the
    end. A naive "the scale went up" check would cry plateau; the smoothed 28-day
    slope is still clearly negative, so the honest verdict is continued loss.
    """
    res = simulate(
        n_days=68,
        true_tdee=2600,
        intake=2100,
        start_weight=85.0,
        water_sd=0.4,
        water_autocorr=0.6,
        seed=1,
    )
    weight = res.observed.weight.copy()
    weight[-4:] += 1.6  # refeed / high-sodium water retention on recent days

    # The raw scale really is rising at the end (this is what fools naive checks).
    assert weight[-1] > weight[-5]

    est = estimate_tdee(weight, res.observed.intake, window_days=28)
    assert est.slope_kg_per_day < 0  # but the smoothed trend still falls
    verdict = classify_plateau(est, expected_deficit_kcal=500)
    assert verdict.state in {"on_track", "slowing"}
    assert verdict.state != "stalled"


def test_shallow_real_loss_is_slowing():
    """A real but small deficit (150 vs an intended 500) -> still losing, but
    slowing.

    Label rationale: they intend 500 kcal but only 150 is real, so the trend is
    genuinely negative yet far shallower than planned — decelerating, not stalled,
    not on pace.
    """
    res = simulate(
        n_days=68,
        true_tdee=2250,
        intake=2100,
        start_weight=85.0,
        water_sd=0.25,
        water_autocorr=0.5,
        seed=1,
    )
    est = estimate_tdee(res.observed.weight, res.observed.intake, window_days=28)
    verdict = classify_plateau(est, expected_deficit_kcal=500)
    assert verdict.state == "slowing"


# --------------------------------------------------------------------------- #
# Deferring to upstream status
# --------------------------------------------------------------------------- #
def test_insufficient_upstream_is_undetermined():
    est = _make_estimate(None, None, status="insufficient_data")
    verdict = classify_plateau(est, expected_deficit_kcal=500)
    assert verdict.state == "undetermined"
    assert verdict.slope_kg_per_day is None
    assert verdict.based_on_status == "insufficient_data"


def test_low_confidence_upstream_is_undetermined():
    """Even with a clearly negative slope, low_confidence must not yield a verdict."""
    est = _make_estimate(-0.06, 0.30, status="low_confidence")
    verdict = classify_plateau(est, expected_deficit_kcal=500)
    assert verdict.state == "undetermined"
    assert verdict.based_on_status == "low_confidence"


# --------------------------------------------------------------------------- #
# The "meaningful deficit" gate
# --------------------------------------------------------------------------- #
def test_flat_without_meaningful_deficit_is_on_track_not_stalled():
    """A flat trend with no real intended deficit is maintenance, not a stall."""
    flat = _make_estimate(0.0, 0.002)  # dead-flat, tight CI
    assert classify_plateau(flat, expected_deficit_kcal=100).state == "on_track"
    # The very same trend, but the user IS cutting hard -> stalled.
    assert classify_plateau(flat, expected_deficit_kcal=500).state == "stalled"


# --------------------------------------------------------------------------- #
# Boundary behaviour — pin the thresholds so future changes are deliberate
# --------------------------------------------------------------------------- #
def test_dead_zone_boundary_is_flat_side():
    """A slope exactly at the dead-zone edge (with a tight CI) counts as flat."""
    dz = DEFAULT_DEAD_ZONE_KG_PER_DAY
    est = _make_estimate(-dz, 1e-9)  # |slope| == dead-zone, negligible SE
    assert classify_plateau(est, expected_deficit_kcal=500).state == "stalled"

    # Just past the dead-zone, confidently losing -> no longer stalled.
    est2 = _make_estimate(-dz - 0.02, 1e-9)
    assert classify_plateau(est2, expected_deficit_kcal=500).state != "stalled"


def test_slowing_vs_on_track_boundary():
    """When confidently losing, the on_track/slowing split is at
    on_track_fraction * intended rate (default 0.5).

    Intended 500 kcal -> expected slope -500/7700 = -0.0649 kg/day; half of that
    is -0.03247. A tight-SE slope just steeper than that is on_track; just
    shallower is slowing.
    """
    threshold = 0.5 * (500.0 / DENSITY)  # 0.03247 kg/day
    steeper = _make_estimate(-(threshold + 0.002), 1e-9)  # losing faster than half-rate
    shallower = _make_estimate(-(threshold - 0.002), 1e-9)  # losing slower than half-rate
    assert classify_plateau(steeper, expected_deficit_kcal=500).state == "on_track"
    assert classify_plateau(shallower, expected_deficit_kcal=500).state == "slowing"


# --------------------------------------------------------------------------- #
# Property-style monotonicity
# --------------------------------------------------------------------------- #
def test_steeper_loss_never_moves_verdict_toward_stalled():
    """Making the trend more steeply negative must never make the verdict *more*
    stalled — severity is monotonic non-increasing in downward slope."""
    severity = {"on_track": 0, "slowing": 1, "stalled": 2, "undetermined": 3}
    slopes = np.linspace(0.02, -0.12, 40)  # from slightly rising to steeply falling
    prev = None
    for slope in slopes:
        est = _make_estimate(float(slope), 0.01)
        sev = severity[classify_plateau(est, expected_deficit_kcal=500).state]
        if prev is not None:
            assert sev <= prev, f"verdict got more stalled at slope {slope:.3f}"
        prev = sev


# --------------------------------------------------------------------------- #
# Evidence fields + validation
# --------------------------------------------------------------------------- #
def test_ok_verdict_exposes_evidence():
    est = _make_estimate(-0.06, 0.01)
    verdict = classify_plateau(est, expected_deficit_kcal=500)
    assert verdict.slope_kg_per_day == pytest.approx(-0.06)
    assert verdict.slope_ci_kg_per_day is not None
    lo, hi = verdict.slope_ci_kg_per_day
    assert lo < -0.06 < hi
    assert verdict.implied_deficit_kcal == pytest.approx(0.06 * DENSITY)
    assert verdict.expected_deficit_kcal == 500


def test_negative_dead_zone_raises():
    est = _make_estimate(-0.05, 0.01)
    with pytest.raises(ValueError):
        classify_plateau(est, dead_zone_kg_per_day=-0.01)
