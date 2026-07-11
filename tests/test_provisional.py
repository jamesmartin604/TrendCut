"""Provisional (day-1 formula) TDEE tests.

The provisional lives OUTSIDE the frozen estimator: a generic Mifflin–St Jeor +
activity-factor estimate used only during cold-start. These tests pin the arithmetic
against hand-computed cases, the band, and the "never a partial number" contract.
"""

from __future__ import annotations

import pytest

from api.provisional import (
    ACTIVITY_FACTORS,
    PROVISIONAL_BAND_HALFWIDTH,
    provisional_tdee,
)


def _complete(**overrides):
    base = dict(age=30, sex="male", height_cm=180.0, weight_kg=80.0, activity_level="moderate")
    base.update(overrides)
    return base


def test_bmr_male_hand_computed() -> None:
    # BMR = 10*80 + 6.25*180 - 5*30 + 5 = 1780; TDEE = 1780 * 1.55 (moderate) = 2759.
    p = provisional_tdee(**_complete())
    assert p is not None
    assert p.tdee == pytest.approx(2759.0)
    assert p.method == "mifflin_st_jeor"


def test_bmr_female_hand_computed() -> None:
    # BMR = 10*65 + 6.25*165 - 5*30 - 161 = 1370.25; TDEE = *1.375 (light) = 1884.09375.
    p = provisional_tdee(
        age=30, sex="female", height_cm=165.0, weight_kg=65.0, activity_level="light"
    )
    assert p is not None
    assert p.tdee == pytest.approx(1370.25 * ACTIVITY_FACTORS["light"])


def test_activity_factor_scales_tdee() -> None:
    # Same body, two activity levels — ratio is exactly the factor ratio.
    sed = provisional_tdee(**_complete(activity_level="sedentary"))
    act = provisional_tdee(**_complete(activity_level="active"))
    assert act.tdee / sed.tdee == pytest.approx(
        ACTIVITY_FACTORS["active"] / ACTIVITY_FACTORS["sedentary"]
    )


def test_band_is_symmetric_and_wide() -> None:
    p = provisional_tdee(**_complete())
    assert p.low == pytest.approx(p.tdee * (1 - PROVISIONAL_BAND_HALFWIDTH))
    assert p.high == pytest.approx(p.tdee * (1 + PROVISIONAL_BAND_HALFWIDTH))
    # The band must be genuinely wide (this is the honesty point): a >= ±20% span so
    # it is clearly wider than a data-derived CI, making the handoff narrowing real.
    assert PROVISIONAL_BAND_HALFWIDTH >= 0.20
    assert p.low < p.tdee < p.high


def test_basis_names_the_activity_caveat() -> None:
    # The displayed caveat must flag the activity guess as the biggest error source.
    p = provisional_tdee(**_complete())
    assert "activity" in p.basis.lower()


@pytest.mark.parametrize(
    "missing",
    ["age", "sex", "height_cm", "weight_kg", "activity_level"],
)
def test_returns_none_when_any_input_missing(missing: str) -> None:
    p = provisional_tdee(**_complete(**{missing: None}))
    assert p is None


def test_returns_none_on_unknown_enums() -> None:
    assert provisional_tdee(**_complete(sex="other")) is None
    assert provisional_tdee(**_complete(activity_level="olympic")) is None


def test_returns_none_on_nonpositive_numbers() -> None:
    assert provisional_tdee(**_complete(age=0)) is None
    assert provisional_tdee(**_complete(height_cm=0)) is None
    assert provisional_tdee(**_complete(weight_kg=-5)) is None
