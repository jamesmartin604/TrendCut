"""Plausibility guards — SOFT warnings, never hard rejections (PRD FR-5).

These check that a logged weight/calorie value is physically plausible and return
human-readable warnings. The caller still writes the value; it just surfaces the
warnings alongside the response, so a fat-fingered ``850 kg`` is flagged without
blocking the user.
"""

from __future__ import annotations

__all__ = ["MAX_PLAUSIBLE_KCAL", "PLAUSIBLE_WEIGHT_KG", "entry_warnings"]

# Generous human bounds — we warn outside these, we do not reject.
PLAUSIBLE_WEIGHT_KG = (20.0, 400.0)
MAX_PLAUSIBLE_KCAL = 20000


def entry_warnings(weight_kg: float | None, kcal: int | None) -> list[str]:
    """Return soft warnings for an entry's values (empty list if all plausible)."""
    warnings: list[str] = []
    lo, hi = PLAUSIBLE_WEIGHT_KG
    if weight_kg is not None and not (lo <= weight_kg <= hi):
        warnings.append(
            f"weight {weight_kg} kg is outside the plausible range {lo:.0f}-{hi:.0f} kg "
            f"— saved anyway; check for a typo."
        )
    if kcal is not None and kcal < 0:
        warnings.append(f"calories {kcal} is negative — saved anyway; check for a typo.")
    if kcal is not None and kcal > MAX_PLAUSIBLE_KCAL:
        warnings.append(
            f"calories {kcal} is implausibly high (> {MAX_PLAUSIBLE_KCAL}) "
            f"— saved anyway; check for a typo."
        )
    return warnings
