"""Runnable demo of the gap-aware EWMA smoother.

Generates a noisy 30-day weigh-in series with the synthetic simulator, runs the
``ewma_trend`` smoother over it, and prints a tiny text chart so you can *see*
the jumpy raw scale (``o``) being pulled onto a calm trend line (``+``).

Run it::

    python -m examples.smoothing_demo
    # or
    python examples/smoothing_demo.py

Edit the parameters in :func:`main` to explore: a bigger ``water_sd`` makes the
``o`` markers jump around more wildly while the ``+`` trend stays smooth; a
smaller ``alpha`` (passed to ``ewma_trend``) smooths harder and lags more.
"""

import numpy as np

from estimator.smoothing import ewma_trend
from estimator.synthetic import simulate

CHART_WIDTH = 44  # columns of horizontal space for the weight axis


def _row(raw: float, trend: float, lo: float, span: float) -> str:
    """Render one day as a text-chart row: 'o' = raw scale, '+' = smoothed trend."""
    cells = [" "] * CHART_WIDTH

    def place(value: float, marker: str) -> None:
        col = int(round((value - lo) / span * (CHART_WIDTH - 1)))
        col = max(0, min(CHART_WIDTH - 1, col))
        # If both markers land in the same cell, show that they coincide.
        cells[col] = "O" if cells[col] not in (" ", marker) else marker

    if not np.isnan(trend):
        place(trend, "+")
    if not np.isnan(raw):
        place(raw, "o")
    return "".join(cells)


def main() -> None:
    res = simulate(
        n_days=30,
        true_tdee=2600,
        intake=1500,  # ~500 kcal/day deficit
        start_weight=85.0,
        water_sd=0.6,  # daily water wobble — turn this up to see more jitter
        spike_rate=0.05,
        missing_weight_rate=0.0,  # set e.g. 0.2 to scatter gaps through the chart
        seed=3,
    )
    raw = res.observed.weight
    trend = ewma_trend(raw)  # default alpha = 0.10 (~10-day window)

    lo, hi = float(np.nanmin(raw)), float(np.nanmax(raw))
    span = max(hi - lo, 1e-9)

    print("EWMA trend smoothing — 'o' = raw scale, '+' = smoothed trend\n")
    print(f"  day |   raw  | trend  | {lo:.1f} kg{' ' * (CHART_WIDTH - 12)}{hi:.1f} kg")
    print(f"  ----+--------+--------+-{'-' * CHART_WIDTH}")
    for day, (r, t) in enumerate(zip(raw, trend, strict=True)):
        raw_txt = "  --  " if np.isnan(r) else f"{r:6.2f}"
        trend_txt = "  --  " if np.isnan(t) else f"{t:6.2f}"
        print(f"  {day:3d} | {raw_txt} | {trend_txt} | {_row(r, t, lo, span)}")
    print("\nThe 'o' markers bounce day to day; the '+' trend glides through them —")
    print("that calm line is the fat-loss signal pulled out of the water-weight noise.")


if __name__ == "__main__":
    main()
