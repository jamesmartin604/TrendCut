"""Plateau detection.

A *graded* classifier — "on_track" / "slowing" / "stalled" (plus an honest
"undetermined") — that reads the TDEE estimator's output and judges whether the
user is genuinely stalled, merely slowing, or losing as planned (PRD §5.4, §6.4,
FR-14..FR-17).

It **consumes** a :class:`estimator.tdee.TDEEEstimate`; it never re-runs the
regression. The only signals it uses are the estimate's smoothed-trend
``slope_kg_per_day`` and ``slope_se`` (from which it forms a slope confidence
interval), ``mean_intake``/``tdee`` (the *currently implied* deficit), the
``assumed_energy_density``, and the upstream ``status``.

Plateau vs. swing — the whole point
-----------------------------------
* A **plateau (stalled)** is a smoothed-trend slope statistically
  indistinguishable from ~zero — its confidence interval overlaps a small
  dead-zone around zero — *while the user intends a meaningful deficit*. Flat
  trend + a real intended deficit = adaptive thermogenesis, under-logging, or
  water masking loss.
* A **swing is NOT a plateau.** A flat or rising *raw* scale caused by water
  retention / a refeed, whose *smoothed* slope is still confidently negative, must
  never be flagged. Because this classifier looks only at the smoothed slope and
  its CI (never the raw scale), a genuine underlying downtrend keeps the slope CI
  below the dead-zone and the verdict stays "on_track" / "slowing".

Why the deficit is a parameter (``expected_deficit_kcal``)
----------------------------------------------------------
When the trend is flat, the estimator's own implied deficit (``tdee -
mean_intake``) is ~0 — it concludes you're at maintenance. So "flat *despite a
deficit*" cannot be read off the current estimate; the deficit must be the
deficit the user *intends* to run (from their diet plan / target — PRD FR-18).
That intended deficit is ``expected_deficit_kcal``. If it is below
``meaningful_deficit_kcal`` the user isn't really cutting, so a flat trend is
treated as maintenance ("on_track"), not a stall.

Thresholds (all parameters, all defaulted)
-------------------------------------------
* ``expected_deficit_kcal`` (300) — the user's intended daily deficit.
* ``meaningful_deficit_kcal`` (150) — below this, there's no real cut to stall.
* ``dead_zone_kg_per_day`` (0.014 ≈ 0.1 kg/week) — slopes this shallow are "flat".
* ``on_track_fraction`` (0.5) — when confidently losing, losing at less than this
  fraction of the intended rate is "slowing" rather than "on_track".
* ``z`` (1.96) — critical value for the slope CI (matches the estimator's CI; note
  that SE inherits the estimator's documented optimism, so the CI is indicative).

Deferring to upstream
---------------------
If the estimate's status is not ``"ok"`` (i.e. ``"insufficient_data"`` or
``"low_confidence"``), the classifier does **not** guess a verdict — it returns
``"undetermined"`` with an explanation. An honest "can't judge" beats a confident
plateau call on shaky data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from estimator.tdee import TDEEEstimate

__all__ = [
    "DEFAULT_DEAD_ZONE_KG_PER_DAY",
    "DEFAULT_EXPECTED_DEFICIT_KCAL",
    "DEFAULT_MEANINGFUL_DEFICIT_KCAL",
    "DEFAULT_ON_TRACK_FRACTION",
    "PlateauAssessment",
    "PlateauState",
    "classify_plateau",
]

PlateauState = Literal["on_track", "slowing", "stalled", "undetermined"]

DEFAULT_EXPECTED_DEFICIT_KCAL = 300.0
DEFAULT_MEANINGFUL_DEFICIT_KCAL = 150.0
DEFAULT_DEAD_ZONE_KG_PER_DAY = 0.014  # ~0.1 kg/week
DEFAULT_ON_TRACK_FRACTION = 0.5
Z_95 = 1.96


@dataclass(frozen=True)
class PlateauAssessment:
    """A graded plateau verdict plus the evidence behind it.

    ``slope_*``/``trend_is_flat``/``implied_deficit_kcal`` are ``None`` when the
    verdict is ``"undetermined"`` (upstream estimate was not usable).
    """

    state: PlateauState
    explanation: str
    slope_kg_per_day: float | None
    slope_ci_kg_per_day: tuple[float, float] | None
    trend_is_flat: bool | None
    implied_deficit_kcal: float | None  # current, from the estimate: tdee - mean_intake
    expected_deficit_kcal: float
    based_on_status: str


def _kg_per_week(kg_per_day: float) -> float:
    return kg_per_day * 7.0


def classify_plateau(
    estimate: TDEEEstimate,
    *,
    expected_deficit_kcal: float = DEFAULT_EXPECTED_DEFICIT_KCAL,
    meaningful_deficit_kcal: float = DEFAULT_MEANINGFUL_DEFICIT_KCAL,
    dead_zone_kg_per_day: float = DEFAULT_DEAD_ZONE_KG_PER_DAY,
    on_track_fraction: float = DEFAULT_ON_TRACK_FRACTION,
    z: float = Z_95,
) -> PlateauAssessment:
    """Grade the current trend as on_track / slowing / stalled / undetermined.

    Parameters
    ----------
    estimate:
        The current :class:`~estimator.tdee.TDEEEstimate`. Its ``status`` gates the
        verdict; only ``"ok"`` estimates get a confident grade.
    expected_deficit_kcal:
        The daily deficit the user *intends* to run (from their plan/target). This
        is the reference for "flat despite a deficit"; see the module docstring.
    meaningful_deficit_kcal, dead_zone_kg_per_day, on_track_fraction, z:
        Thresholds; see the module docstring.

    Returns
    -------
    PlateauAssessment
    """
    if dead_zone_kg_per_day < 0.0:
        raise ValueError(f"dead_zone_kg_per_day must be >= 0, got {dead_zone_kg_per_day}")

    # Defer to upstream: never assert a verdict on non-"ok" data.
    if estimate.status != "ok":
        return PlateauAssessment(
            state="undetermined",
            explanation=(
                f"Can't judge a plateau: the TDEE estimate is '{estimate.status}' "
                f"({estimate.n_valid_weight_days} weigh-ins, "
                f"{estimate.n_valid_intake_days} intake days in the "
                f"{estimate.window_days}-day window) — not enough reliable signal."
            ),
            slope_kg_per_day=None,
            slope_ci_kg_per_day=None,
            trend_is_flat=None,
            implied_deficit_kcal=None,
            expected_deficit_kcal=expected_deficit_kcal,
            based_on_status=estimate.status,
        )

    # From here the estimate is "ok", so slope, slope_se, tdee, mean_intake are
    # all populated (guaranteed by the estimator's status contract).
    slope = estimate.slope_kg_per_day
    se = estimate.slope_se
    density = estimate.assumed_energy_density

    ci_low = slope - z * se
    ci_high = slope + z * se
    dz = dead_zone_kg_per_day

    implied_deficit = estimate.tdee - estimate.mean_intake  # = -slope * density
    expected_slope = -expected_deficit_kcal / density  # intended kg/day (<= 0)
    meaningful = expected_deficit_kcal >= meaningful_deficit_kcal

    # Slope zones relative to the dead-zone band [-dz, +dz].
    confidently_losing = ci_high < -dz  # whole CI below the flat band
    flat_or_up = slope >= -dz  # point estimate not meaningfully downward
    # possibly_losing: point is down but the CI still reaches into the flat band.
    trend_is_flat = (ci_high >= -dz) and (ci_low <= dz)

    rate_wk = _kg_per_week(slope)
    exp_rate_wk = _kg_per_week(expected_slope)

    if confidently_losing:
        if meaningful and abs(slope) < on_track_fraction * abs(expected_slope):
            state: PlateauState = "slowing"
            explanation = (
                f"Slowing: the {estimate.window_days}-day trend is still falling "
                f"(~{abs(rate_wk):.2f} kg/week) but well under the ~{abs(exp_rate_wk):.2f} "
                f"kg/week your intended {expected_deficit_kcal:.0f} kcal/day deficit implies "
                f"— loss is decelerating."
            )
        else:
            state = "on_track"
            explanation = (
                f"On track: the {estimate.window_days}-day trend is falling "
                f"~{abs(rate_wk):.2f} kg/week, consistent with continued loss."
            )
    elif not flat_or_up:  # possibly_losing: down but not confident
        state = "slowing"
        explanation = (
            f"Slowing: the trend points to loss (~{abs(rate_wk):.2f} kg/week) but the data "
            f"is too noisy to rule out a flat trend (slope CI "
            f"{_kg_per_week(ci_low):+.2f}..{_kg_per_week(ci_high):+.2f} kg/week)."
        )
    else:  # flat or rising
        if meaningful:
            state = "stalled"
            shape = "flat" if slope <= dz else "flat-to-rising"
            explanation = (
                f"Stalled: the {estimate.window_days}-day trend is {shape} (slope CI "
                f"{_kg_per_week(ci_low):+.2f}..{_kg_per_week(ci_high):+.2f} kg/week includes "
                f"zero) despite an intended ~{expected_deficit_kcal:.0f} kcal/day deficit — "
                f"consistent with adaptive thermogenesis, under-logging, or water masking loss."
            )
        else:
            state = "on_track"
            explanation = (
                f"On track: the trend is flat but no meaningful deficit is intended "
                f"(~{expected_deficit_kcal:.0f} kcal/day) — this reads as maintenance, "
                f"not a stall."
            )

    return PlateauAssessment(
        state=state,
        explanation=explanation,
        slope_kg_per_day=slope,
        slope_ci_kg_per_day=(ci_low, ci_high),
        trend_is_flat=trend_is_flat,
        implied_deficit_kcal=implied_deficit,
        expected_deficit_kcal=expected_deficit_kcal,
        based_on_status=estimate.status,
    )
