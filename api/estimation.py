"""Glue between stored entries and the pure estimator package.

The estimator works on daily-aligned arrays where *array position is the day
index*, but entries are stored sparsely (only days the user logged). So this
module reindexes entries onto a continuous daily calendar from the first to the
last logged date, inserting ``NaN`` for gaps, then calls the estimator.

This is pure read-and-compute: it stores nothing. Every call recomputes the trend,
TDEE, and plateau verdict from the raw entries (PRD §9.1). It faithfully surfaces
the estimator's honest statuses — it never substitutes a fallback number when the
estimator declines to estimate.
"""

from __future__ import annotations

import datetime as _dt
import math

import numpy as np

from api.models import (
    Derivation,
    EstimateResponse,
    PlateauResult,
    ProvisionalTDEE,
    TdeeHistoryPoint,
    TdeeHistoryResponse,
    TdeeResult,
    TrendPoint,
)
from api.provisional import provisional_tdee
from api.storage import Entry, Settings
from estimator.plateau import classify_plateau
from estimator.smoothing import ewma_trend
from estimator.tdee import LOW_CONF_HALFWIDTH_KCAL, PLAUSIBLE_TDEE, TDEEEstimate, estimate_tdee

__all__ = ["compute_estimate", "compute_tdee_history", "entries_to_series"]


def _derivation(est: TDEEEstimate, settings: Settings) -> Derivation | None:
    """Bundle the real values that produced this estimate, for the transparency panel.

    Pure re-exposure: every field is copied from the estimator's own result (or the α
    it was run with) — nothing is recomputed here, so the panel can never disagree
    with the headline. ``None`` when the estimator withheld a number.
    """
    if est.tdee is None or est.slope_kg_per_day is None or est.mean_intake is None:
        return None
    return Derivation(
        mean_intake_kcal=est.mean_intake,
        slope_kg_per_day=est.slope_kg_per_day,
        slope_se=est.slope_se,
        energy_density=est.assumed_energy_density,
        alpha=settings.alpha,
        # The OLS fit runs on exactly the valid weigh-in days in the window.
        n_regression_points=est.n_valid_weight_days,
    )


def _low_confidence_reason(est: TDEEEstimate) -> str | None:
    """WHY the estimator flagged low_confidence — re-derived from its OWN returned CI
    and the estimator's own threshold constants (imported read-only), so it always
    agrees with ``status``. NOTE the coupling: these thresholds mirror
    ``estimate_tdee``'s defaults; if that function starts overriding them this must
    follow. Returns None unless status is low_confidence.
    """
    if est.status != "low_confidence" or est.tdee is None:
        return None
    halfwidth = est.ci_high - est.tdee  # the estimator's own CI half-width
    wide = halfwidth > LOW_CONF_HALFWIDTH_KCAL
    implausible = not (PLAUSIBLE_TDEE[0] <= est.tdee <= PLAUSIBLE_TDEE[1])
    if wide and implausible:
        return "wide_interval_and_implausible"
    if wide:
        return "wide_interval"
    if implausible:
        return "implausible_value"
    return None  # status was low_confidence for no re-derivable reason (shouldn't happen)


def _latest_weight_kg(entries: list[Entry]) -> float | None:
    """The most recent logged weigh-in (entries are date-ordered), or None.

    This is the anchor for the provisional day-1 estimate. We deliberately use the
    latest weigh-in as-is with no recency cutoff: the provisional carries a wide
    population band and is retired the moment the real estimate arrives, so a
    somewhat stale anchor is an acceptable, short-lived approximation rather than
    something worth gating on.
    """
    for entry in reversed(entries):
        if entry.weight_kg is not None:
            return entry.weight_kg
    return None


def _provisional(entries: list[Entry], settings: Settings, status: str) -> ProvisionalTDEE | None:
    """The opt-in provisional estimate, or None.

    Present ONLY while the real estimator withholds a number
    (``status == "insufficient_data"``) AND the user has complete demographics AND a
    weigh-in exists to anchor the formula. It is never returned alongside a real
    estimate (``ok``/``low_confidence``) — the two are never both authoritative.
    """
    if status != "insufficient_data":
        return None
    p = provisional_tdee(
        age=settings.age,
        sex=settings.sex,
        height_cm=settings.height_cm,
        weight_kg=_latest_weight_kg(entries),
        activity_level=settings.activity_level,
    )
    if p is None:
        return None
    return ProvisionalTDEE(tdee=p.tdee, low=p.low, high=p.high, method=p.method, basis=p.basis)


def entries_to_series(
    entries: list[Entry],
) -> tuple[list[_dt.date], np.ndarray, np.ndarray]:
    """Reindex sparse entries onto a continuous daily calendar.

    Returns ``(dates, weight, intake)`` where all three are the same length, one
    element per calendar day between the first and last logged date (inclusive),
    with ``NaN`` for any day/field without a value. Empty input yields empty arrays.
    """
    if not entries:
        return [], np.empty(0), np.empty(0)

    by_date = {_dt.date.fromisoformat(e.date): e for e in entries}
    start, end = min(by_date), max(by_date)
    n_days = (end - start).days + 1

    dates = [start + _dt.timedelta(days=i) for i in range(n_days)]
    weight = np.full(n_days, np.nan)
    intake = np.full(n_days, np.nan)
    for i, day in enumerate(dates):
        entry = by_date.get(day)
        if entry is None:
            continue
        if entry.weight_kg is not None:
            weight[i] = entry.weight_kg
        if entry.kcal is not None:
            intake[i] = entry.kcal
    return dates, weight, intake


def _clean(value: float) -> float | None:
    """NaN -> None so the result is valid JSON."""
    return None if math.isnan(value) else float(value)


def compute_estimate(entries: list[Entry], settings: Settings) -> EstimateResponse:
    """Recompute trend + TDEE + plateau from raw entries and settings."""
    dates, weight, intake = entries_to_series(entries)

    trend = ewma_trend(weight, alpha=settings.alpha) if dates else np.empty(0)
    trend_points = [
        TrendPoint(date=day.isoformat(), weight_kg=_clean(w), trend_kg=_clean(t))
        for day, w, t in zip(dates, weight, trend, strict=True)
    ]

    est = estimate_tdee(
        weight,
        intake,
        window_days=settings.window_days,
        alpha=settings.alpha,
        assumed_energy_density=settings.energy_density,
    )
    verdict = classify_plateau(est, expected_deficit_kcal=settings.expected_deficit_kcal)

    return EstimateResponse(
        window_days=settings.window_days,
        alpha=settings.alpha,
        energy_density=settings.energy_density,
        n_entries=len(entries),
        date_start=dates[0].isoformat() if dates else None,
        date_end=dates[-1].isoformat() if dates else None,
        trend=trend_points,
        tdee=TdeeResult(
            status=est.status,
            tdee=est.tdee,
            ci_low=est.ci_low,
            ci_high=est.ci_high,
            slope_kg_per_day=est.slope_kg_per_day,
            slope_se=est.slope_se,
            mean_intake=est.mean_intake,
            window_days=est.window_days,
            n_valid_weight_days=est.n_valid_weight_days,
            n_valid_intake_days=est.n_valid_intake_days,
            assumed_energy_density=est.assumed_energy_density,
            derivation=_derivation(est, settings),
            low_confidence_reason=_low_confidence_reason(est),
        ),
        plateau=PlateauResult(
            state=verdict.state,
            explanation=verdict.explanation,
            slope_kg_per_day=verdict.slope_kg_per_day,
            slope_ci_kg_per_day=verdict.slope_ci_kg_per_day,
            trend_is_flat=verdict.trend_is_flat,
            implied_deficit_kcal=verdict.implied_deficit_kcal,
            expected_deficit_kcal=verdict.expected_deficit_kcal,
            based_on_status=verdict.based_on_status,
        ),
        provisional=_provisional(entries, settings, est.status),
    )


def compute_tdee_history(entries: list[Entry], settings: Settings) -> TdeeHistoryResponse:
    """Recompute the rolling TDEE estimate *as of* each past day.

    For every calendar day in the series, the estimator is run on only the data up
    to and including that day (a truncated prefix), reproducing what the estimate
    would have been on that date. The result is the drift of the rolling estimate
    over time — the visual signature of adaptive thermogenesis (PRD FR-21).

    Like :func:`compute_estimate`, this stores nothing and recomputes from raw
    entries. Each point carries the estimator's own ``status``, so early days honestly
    surface as ``insufficient_data`` (``tdee`` is ``None``) rather than a fabricated
    number — the frontend breaks the line across those gaps.

    Cost is ``O(n_days)`` estimator calls; for a single user's history that is a few
    hundred cheap calls, well under a second.
    """
    dates, weight, intake = entries_to_series(entries)

    points: list[TdeeHistoryPoint] = []
    for i in range(len(dates)):
        est = estimate_tdee(
            weight[: i + 1],
            intake[: i + 1],
            window_days=settings.window_days,
            alpha=settings.alpha,
            assumed_energy_density=settings.energy_density,
        )
        points.append(
            TdeeHistoryPoint(
                date=dates[i].isoformat(),
                status=est.status,
                tdee=est.tdee,
                ci_low=est.ci_low,
                ci_high=est.ci_high,
            )
        )

    return TdeeHistoryResponse(
        window_days=settings.window_days,
        alpha=settings.alpha,
        energy_density=settings.energy_density,
        points=points,
    )
