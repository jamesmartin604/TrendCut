"""Provisional (day-1) TDEE from a population formula — the opt-in cold-start bridge.

This is deliberately OUTSIDE :mod:`estimator` (which is the frozen, ground-truth-
validated data-derived engine). The provisional is a *generic* population estimate
used only while the real estimator has too little data to speak, and it is retired
the moment the real estimate becomes available. It is never blended into or
presented as the data-derived number.

Method
------
Mifflin–St Jeor resting metabolic rate, then a coarse activity multiplier:

    BMR = 10*weight_kg + 6.25*height_cm - 5*age + s     (s = +5 male, -161 female)
    TDEE = BMR * activity_factor

Honest uncertainty band (this is the crux)
------------------------------------------
The band must reflect the formula's *known population-level* error and be clearly
WIDER than a data-derived CI, so the narrowing at handoff is real, not cosmetic.

Mifflin–St Jeor predicts measured BMR to within ~10% for ~82% of individuals, i.e.
a ~1-SD relative error near 10% (Frankenfield, Roth-Yousey & Compher, *J Am Diet
Assoc* 2005). The activity multiplier is a much coarser guess — picking one of five
buckets for a whole lifestyle — and is realistically the single largest error
source; we treat it as another ~10% (1 SD). Combining the two independent relative
errors in quadrature gives ~14% at 1 SD, i.e. ~±28% at ~95%. We use a rounded
``±25%`` half-width for the displayed band — comfortably wider than the estimator's
own low-confidence boundary (a 500 kcal half-width on a typical ~2500 kcal TDEE is
±20%, and a confident data-derived CI is far tighter).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ACTIVITY_FACTORS",
    "PROVISIONAL_BAND_HALFWIDTH",
    "Provisional",
    "provisional_tdee",
]

# Standard Mifflin–St Jeor activity multipliers.
ACTIVITY_FACTORS = {
    "sedentary": 1.2,  # little/no exercise
    "light": 1.375,  # light exercise 1-3 days/week
    "moderate": 1.55,  # moderate exercise 3-5 days/week
    "active": 1.725,  # hard exercise 6-7 days/week
    "very_active": 1.9,  # very hard exercise / physical job
}

_VALID_SEX = {"male", "female"}

# Relative half-width of the population band (see module docstring for the basis).
PROVISIONAL_BAND_HALFWIDTH = 0.25

_METHOD = "mifflin_st_jeor"
# Surfaced to the UI as the honest caveat. Leads with the activity guess because
# that bucket choice is the biggest error source.
_BASIS = (
    "Generic estimate from the Mifflin–St Jeor formula and your activity level — "
    "the activity setting is a rough guess and the largest source of error. Not "
    "based on your logged data; expect a wide range until the real estimate arrives."
)


@dataclass(frozen=True)
class Provisional:
    tdee: float
    low: float
    high: float
    method: str
    basis: str


def provisional_tdee(
    *,
    age: int | None,
    sex: str | None,
    height_cm: float | None,
    weight_kg: float | None,
    activity_level: str | None,
) -> Provisional | None:
    """Compute the provisional TDEE, or ``None`` if inputs are incomplete/invalid.

    Never raises and never returns a partial number: if any input is missing, the
    sex/activity value is unrecognised, or a numeric input is non-positive, the
    result is ``None`` and the caller falls back to the honest "not enough data"
    state. All five inputs (including a current ``weight_kg``, sourced by the caller
    from the latest weigh-in) are required.
    """
    if age is None or sex is None or height_cm is None or weight_kg is None:
        return None
    if activity_level not in ACTIVITY_FACTORS or sex not in _VALID_SEX:
        return None
    if age <= 0 or height_cm <= 0 or weight_kg <= 0:
        return None

    s = 5 if sex == "male" else -161
    bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age + s
    tdee = bmr * ACTIVITY_FACTORS[activity_level]
    if tdee <= 0:  # pathological inputs (e.g. huge age) — decline rather than lie
        return None

    half = tdee * PROVISIONAL_BAND_HALFWIDTH
    return Provisional(
        tdee=tdee,
        low=tdee - half,
        high=tdee + half,
        method=_METHOD,
        basis=_BASIS,
    )
