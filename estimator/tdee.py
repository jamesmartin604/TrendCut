"""TDEE estimation.

Rolling linear regression on the *smoothed* weight series to recover a slope
(kg/day), combined with mean daily intake via the energy-balance relation to back
out current TDEE — with the slope's uncertainty propagated into a confidence
interval (PRD §5.1, §6.3, §7.1 steps 3-5).

The method
----------
1. Smooth the full weight history with the Phase 2 gap-aware EWMA
   (:func:`estimator.smoothing.ewma_trend`) to get the trend-weight line. The
   regression runs on the trend, never on raw scale weight (PRD §6.3 / §7.1).
2. Take the last ``window_days`` days (default 14). Fit an ordinary
   least-squares line of trend-weight vs. day on the days that actually have a
   weigh-in, giving ``slope_kg_per_day`` and its standard error.
3. Average intake over the available logged days in the same window.
4. Convert via energy balance::

       TDEE = mean_intake - slope_kg_per_day * assumed_energy_density

   Losing weight gives a negative slope, so estimated TDEE comes out *above*
   intake — exactly as physics requires (PRD §5.1).
5. Propagate the slope SE into a TDEE confidence interval:
   ``TDEE_se = slope_se * assumed_energy_density``, CI = ``TDEE +/- z * TDEE_se``.

The estimator's assumption is its own (anti-circularity)
--------------------------------------------------------
``assumed_energy_density`` (default :data:`DEFAULT_ASSUMED_ENERGY_DENSITY`,
7700 kcal/kg) is the estimator's *assumption* about how energy converts to body
mass. It is deliberately a separate constant from the simulator's
``true_energy_density`` and is never shared with it. When the two differ, the
estimate is biased by a *known, physical* amount — see
``tests/test_tdee.py::test_density_mismatch_biases_as_expected`` — not a bug:

    bias = TDEE_est - true_TDEE = deficit * (assumed_density - true_density) / true_density

Honest caveat: the confidence interval is optimistic
----------------------------------------------------
The OLS standard error assumes independent residuals, but we fit on an
already-EWMA-smoothed (hence strongly autocorrelated) series, so the naive OLS SE
*understates* the true uncertainty and the reported CI is **too narrow**. We also
use a fixed normal critical value (``z = 1.96``) rather than a t-quantile. This is
acceptable for Phase 3, but the CI should be read as indicative, not exact. A
robust / autocorrelation-aware (e.g. Newey-West or block-bootstrap) standard error
is a documented future enhancement.

Status / graceful insufficiency
--------------------------------
The estimator never raises on degenerate input; it classifies it. ``status`` is
one of:

* ``"insufficient_data"`` — fewer than ``min_weight_days`` logged weigh-ins or
  fewer than ``min_intake_days`` logged intake days in the window, or the weigh-in
  days don't span enough time to define a slope. ``tdee`` is ``None``.
* ``"low_confidence"`` — an estimate exists but the CI half-width exceeds
  ``low_conf_halfwidth_kcal`` (default 500 kcal) or the point estimate falls
  outside the plausible band ``plausible_tdee`` (default 800-6000 kcal). The
  numbers are still returned, but flagged.
* ``"ok"`` — an estimate within those bounds.

Default thresholds (all tunable via arguments): ``min_weight_days = 10`` and
``min_intake_days = 10`` (PRD §7.2 "~10 valid days in window");
``low_conf_halfwidth_kcal = 500`` (a single bad week must not masquerade as a
confident 1000-kcal swing, PRD §7.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike

from estimator.smoothing import DEFAULT_ALPHA, ewma_trend

__all__ = [
    "DEFAULT_ASSUMED_ENERGY_DENSITY",
    "TDEEEstimate",
    "estimate_tdee",
]

# The estimator's OWN assumption — intentionally NOT imported from synthetic.py.
DEFAULT_ASSUMED_ENERGY_DENSITY = 7700.0

# Default thresholds (documented in the module docstring; all overridable).
MIN_WEIGHT_DAYS = 10
MIN_INTAKE_DAYS = 10
LOW_CONF_HALFWIDTH_KCAL = 500.0
PLAUSIBLE_TDEE = (800.0, 6000.0)
Z_95 = 1.96  # normal approximation; see the "optimistic CI" caveat above.

Status = Literal["ok", "low_confidence", "insufficient_data"]


@dataclass(frozen=True)
class TDEEEstimate:
    """Result of a TDEE estimate over a rolling window.

    ``tdee``/``ci_low``/``ci_high``/``slope_*``/``mean_intake`` are ``None`` when
    ``status == "insufficient_data"``. The diagnostic counts and ``window_days``
    are always populated so a caller can explain *why* it could not estimate.
    """

    tdee: float | None
    ci_low: float | None
    ci_high: float | None
    slope_kg_per_day: float | None
    slope_se: float | None
    mean_intake: float | None
    window_days: int
    n_valid_weight_days: int
    n_valid_intake_days: int
    assumed_energy_density: float
    status: Status


def _ols_slope_se(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Return (slope, slope_se, sxx) for an OLS fit of y on x.

    ``slope_se`` is the classic homoskedastic OLS standard error. ``sxx`` (the
    time spread) is returned so the caller can detect a degenerate fit.
    """
    n = x.shape[0]
    x_centered = x - x.mean()
    sxx = float(x_centered @ x_centered)
    slope = float((x_centered @ (y - y.mean())) / sxx)
    intercept = float(y.mean() - slope * x.mean())
    residuals = y - (intercept + slope * x)
    dof = n - 2
    s2 = float(residuals @ residuals) / dof
    slope_se = float(np.sqrt(s2 / sxx))
    return slope, slope_se, sxx


def estimate_tdee(
    weights: ArrayLike,
    intake: ArrayLike,
    *,
    window_days: int = 14,
    alpha: float = DEFAULT_ALPHA,
    assumed_energy_density: float = DEFAULT_ASSUMED_ENERGY_DENSITY,
    min_weight_days: int = MIN_WEIGHT_DAYS,
    min_intake_days: int = MIN_INTAKE_DAYS,
    low_conf_halfwidth_kcal: float = LOW_CONF_HALFWIDTH_KCAL,
    plausible_tdee: tuple[float, float] = PLAUSIBLE_TDEE,
    z: float = Z_95,
) -> TDEEEstimate:
    """Estimate current TDEE from daily weight + intake over a rolling window.

    Parameters
    ----------
    weights, intake:
        Equal-length daily series (one entry per day, ``NaN``/``None`` for a
        missing weigh-in or unlogged day). Array position is the day index.
    window_days:
        Number of most-recent days the regression and the intake mean use.
    alpha:
        EWMA smoothing factor for the trend (default :data:`~estimator.smoothing.DEFAULT_ALPHA`).
        The whole history is smoothed; only the last ``window_days`` are regressed.
    assumed_energy_density:
        The estimator's kcal-per-kg assumption (see module docstring). Distinct
        from the simulator's truth on purpose.
    min_weight_days, min_intake_days, low_conf_halfwidth_kcal, plausible_tdee, z:
        Status thresholds and the CI critical value; see the module docstring.

    Returns
    -------
    TDEEEstimate
        Never raises on degenerate data — it sets ``status`` instead.
    """
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")
    if not 0.0 < alpha <= 1.0:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")
    if assumed_energy_density <= 0.0:
        raise ValueError(f"assumed_energy_density must be > 0, got {assumed_energy_density}")

    w = np.asarray(weights, dtype=float)
    cal = np.asarray(intake, dtype=float)
    if w.ndim != 1 or cal.ndim != 1:
        raise ValueError("weights and intake must be 1-dimensional")
    if w.shape[0] != cal.shape[0]:
        raise ValueError(
            f"weights and intake must be the same length, got {w.shape[0]} and {cal.shape[0]}"
        )

    # Smooth the FULL history so the trend at the window start carries proper
    # warm-up; the regression then uses only the last `window_days`.
    trend = ewma_trend(w, alpha=alpha)

    n = w.shape[0]
    start = max(0, n - window_days)
    local_day = np.arange(start, n, dtype=float)
    w_win = w[start:n]
    trend_win = trend[start:n]
    cal_win = cal[start:n]

    weight_mask = ~np.isnan(w_win) & ~np.isnan(trend_win)
    intake_mask = ~np.isnan(cal_win)
    n_w = int(weight_mask.sum())
    n_i = int(intake_mask.sum())

    def _insufficient() -> TDEEEstimate:
        return TDEEEstimate(
            tdee=None,
            ci_low=None,
            ci_high=None,
            slope_kg_per_day=None,
            slope_se=None,
            mean_intake=None,
            window_days=window_days,
            n_valid_weight_days=n_w,
            n_valid_intake_days=n_i,
            assumed_energy_density=assumed_energy_density,
            status="insufficient_data",
        )

    # Need enough logged days, and at least 3 weigh-ins to get a slope SE (dof>=1).
    if n_w < max(min_weight_days, 3) or n_i < min_intake_days:
        return _insufficient()

    x = local_day[weight_mask]
    y = trend_win[weight_mask]
    if float((x - x.mean()) @ (x - x.mean())) == 0.0:
        # Weigh-ins don't span any time — slope undefined (near-zero variance).
        return _insufficient()

    slope, slope_se, _ = _ols_slope_se(x, y)
    mean_intake = float(cal_win[intake_mask].mean())

    tdee = mean_intake - slope * assumed_energy_density
    tdee_se = slope_se * assumed_energy_density
    halfwidth = z * tdee_se
    ci_low = tdee - halfwidth
    ci_high = tdee + halfwidth

    status: Status = "ok"
    if halfwidth > low_conf_halfwidth_kcal or not (plausible_tdee[0] <= tdee <= plausible_tdee[1]):
        status = "low_confidence"

    return TDEEEstimate(
        tdee=tdee,
        ci_low=ci_low,
        ci_high=ci_high,
        slope_kg_per_day=slope,
        slope_se=slope_se,
        mean_intake=mean_intake,
        window_days=window_days,
        n_valid_weight_days=n_w,
        n_valid_intake_days=n_i,
        assumed_energy_density=assumed_energy_density,
        status=status,
    )
