"""Pydantic request/response models — the API's JSON contract."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "AuthMeResponse",
    "EntryResponse",
    "EntryUpsert",
    "EstimateResponse",
    "ImportResult",
    "LoginRequest",
    "PlateauResult",
    "RegisterRequest",
    "SettingsResponse",
    "SettingsUpdate",
    "TdeeHistoryPoint",
    "TdeeHistoryResponse",
    "TdeeResult",
    "TrendPoint",
]


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


class SettingsResponse(BaseModel):
    alpha: float
    window_days: int
    energy_density: float
    expected_deficit_kcal: float
    target_weight: float | None
    target_date: str | None
    units: str


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
