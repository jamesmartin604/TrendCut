"""Runnable demo of the TDEE estimator — the project's headline trick.

The simulator invents a dieter with a *secret* true TDEE and emits only a noisy,
gappy diary (daily weight + calories). The estimator never sees the secret; it
backs out TDEE from the energy-balance relationship between intake and the
smoothed weight trend, and reports a confidence range and an honesty ``status``.

This script runs several independent dieters (different random diaries, same
secret) so you can see both the accuracy and the spread.

Run it::

    python -m examples.tdee_demo
    # or
    python examples/tdee_demo.py

Edit the constants in :func:`main` to explore: raise ``WATER_SD`` or
``MISSING_RATE`` to make the diary noisier (error grows, the CI widens); shrink
``WINDOW_DAYS`` to starve the estimator (watch the status fall away from "ok").
"""

import numpy as np

from estimator.synthetic import simulate
from estimator.tdee import estimate_tdee

SECRET_TDEE = 2500  # hidden from the estimator
INTAKE = 2000  # eaten per day -> a ~500 kcal/day deficit
WINDOW_DAYS = 28
WATER_SD = 0.4  # daily water-weight noise (kg) — crank this up to see errors grow
MISSING_RATE = 0.1  # fraction of weigh-ins skipped
N_DIETERS = 6


def main() -> None:
    print(f"Secret true TDEE = {SECRET_TDEE} kcal/day (the estimator never sees this)")
    print(f"Each dieter eats {INTAKE} kcal/day; estimate uses the last {WINDOW_DAYS} days.\n")
    print("  dieter | est TDEE | 95% range      | error  | status")
    print("  -------+----------+----------------+--------+-------------")

    errors = []
    for seed in range(N_DIETERS):
        res = simulate(
            n_days=WINDOW_DAYS + 40,  # warm-up history before the window
            true_tdee=SECRET_TDEE,
            intake=INTAKE,
            start_weight=85.0,
            water_sd=WATER_SD,
            water_autocorr=0.7,
            spike_rate=0.0,
            missing_weight_rate=MISSING_RATE,
            seed=seed,
        )
        est = estimate_tdee(res.observed.weight, res.observed.intake, window_days=WINDOW_DAYS)
        if est.tdee is None:
            print(f"  {seed:^6d} |    --    |       --       |   --   | {est.status}")
            continue
        err = est.tdee - SECRET_TDEE
        errors.append(err)
        ci = f"[{est.ci_low:.0f}, {est.ci_high:.0f}]"
        print(f"  {seed:^6d} | {est.tdee:7.0f}  | {ci:>14s} | {err:+5.0f}  | {est.status}")

    errors = np.array(errors)
    print()
    print(
        f"Recovered {len(errors)}/{N_DIETERS} dieters — "
        f"mean error {errors.mean():+.0f} kcal, worst {errors[np.abs(errors).argmax()]:+.0f} kcal."
    )
    print("The estimator backs out a number it was never told, from noise alone.")


if __name__ == "__main__":
    main()
