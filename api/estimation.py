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
    EstimateResponse,
    PlateauResult,
    TdeeHistoryPoint,
    TdeeHistoryResponse,
    TdeeResult,
    TrendPoint,
)
from api.storage import Entry, Settings
from estimator.plateau import classify_plateau
from estimator.smoothing import ewma_trend
from estimator.tdee import estimate_tdee

__all__ = ["compute_estimate", "compute_tdee_history", "entries_to_series"]


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
