"""Pydantic request/response models — the API's JSON contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "AuthMeResponse",
    "EntryResponse",
    "EntryUpsert",
    "EstimateRequirements",
    "EstimateResponse",
    "ImportResult",
    "LoginRequest",
    "PlateauResult",
    "ProvisionalTDEE",
    "RegisterRequest",
    "SettingsResponse",
    "SettingsUpdate",
    "TdeeHistoryPoint",
    "TdeeHistoryResponse",
    "TdeeResult",
    "TrendPoint",
]

# Enumerations for the optional demographic settings (Phase 4). Kept as Literals so
# an out-of-set value is a clean 422 at the request boundary.
Sex = Literal["male", "female"]
ActivityLevel = Literal["sedentary", "light", "moderate", "active", "very_active"]


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str


class AuthMeResponse(BaseModel):
    authenticated: bool
    email: str | None = None


class EntryUpsert(BaseModel):
    weight_kg: float | None = None
    kcal: int | None = None


class EntryResponse(BaseModel):
    date: str
    weight_kg: float | None
    kcal: int | None
    created_at: str
    updated_at: str
    warnings: list[str] = []


class SettingsUpdate(BaseModel):
    alpha: float | None = None
    window_days: int | None = None
    energy_density: float | None = None
    expected_deficit_kcal: float | None = None
    target_weight: float | None = None
    target_date: str | None = None
    units: str | None = None
    # Optional demographics for the day-1 provisional. Ranges are generous plausible
    # human bounds; enums are validated by the Literal types. An out-of-range/bad
    # value is a 422, so the stored demographics can't be garbage.
    age: int | None = Field(default=None, ge=14, le=100)
    sex: Sex | None = None
    height_cm: float | None = Field(default=None, ge=120, le=250)
    activity_level: ActivityLevel | None = None


class SettingsResponse(BaseModel):
    alpha: float
    window_days: int
    energy_density: float
    expected_deficit_kcal: float
    target_weight: float | None
    target_date: str | None
    units: str
    age: int | None = None
    sex: str | None = None
    height_cm: float | None = None
    activity_level: str | None = None


class ImportResult(BaseModel):
    imported: int
    warnings: list[str] = []


class TrendPoint(BaseModel):
    date: str
    weight_kg: float | None
    trend_kg: float | None


class TdeeResult(BaseModel):
    status: str
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


class PlateauResult(BaseModel):
    state: str
    explanation: str
    slope_kg_per_day: float | None
    slope_ci_kg_per_day: tuple[float, float] | None
    trend_is_flat: bool | None
    implied_deficit_kcal: float | None
    expected_deficit_kcal: float
    based_on_status: str


class EstimateRequirements(BaseModel):
    """The estimator's minimum data bar, surfaced for honest onboarding copy.

    These are the real thresholds the estimator uses (not a hardcoded frontend
    guess), so the "you have X of N days" message can never drift from the math.
    """

    min_weight_days: int
    min_intake_days: int


class ProvisionalTDEE(BaseModel):
    """Opt-in day-1 estimate from a population formula (Mifflin–St Jeor + activity).

    Present ONLY during cold-start (``tdee.status == "insufficient_data"``) when the
    user has supplied complete demographics. It is a generic starting estimate, not
    derived from the user's data, and is retired once the real estimate exists — the
    ``basis`` string carries that caveat for display.
    """

    tdee: float
    low: float
    high: float
    method: str
    basis: str


class EstimateResponse(BaseModel):
    window_days: int
    alpha: float
    energy_density: float
    n_entries: int
    date_start: str | None
    date_end: str | None
    trend: list[TrendPoint]
    tdee: TdeeResult
    plateau: PlateauResult
    # Present only during cold-start with complete demographics; None otherwise.
    provisional: ProvisionalTDEE | None = None


class TdeeHistoryPoint(BaseModel):
    """One as-of-day rolling TDEE estimate. ``tdee``/``ci_*`` are ``None`` when the
    estimator returned ``insufficient_data`` for the window ending on ``date``;
    ``status`` always carries the estimator's honest verdict for that day."""

    date: str
    status: str
    tdee: float | None
    ci_low: float | None
    ci_high: float | None


class TdeeHistoryResponse(BaseModel):
    window_days: int
    alpha: float
    energy_density: float
    points: list[TdeeHistoryPoint]
