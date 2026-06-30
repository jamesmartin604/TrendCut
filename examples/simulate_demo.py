"""Runnable demo of the synthetic ground-truth generator.

The generator does not *consume* a weight diary — it *produces* one from a known
hidden TDEE, so that later phases can hand the observed series to the estimator
and check it recovers the answer. This script just generates a diary and prints
it, side by side with the hidden truth, so you can see the realistic noise.

Run it::

    python -m examples.simulate_demo
    # or
    python examples/simulate_demo.py

Edit the parameters in :func:`main` to explore: bigger ``water_sd`` and
``spike_rate`` simulate a "bad week"; ``water_autocorr=0.0`` makes the noise
i.i.d. (the wrong assumption naive estimators make); pass a list or callable as
``intake`` to model weekly refeeds.
"""

import numpy as np

from estimator.synthetic import simulate


def main() -> None:
    res = simulate(
        n_days=120,
        true_tdee=2600,  # the metabolism we secretly hide from the estimator
        intake=2100,  # steady ~500 kcal/day deficit (try a lambda for refeeds)
        start_weight=85.0,
        water_sd=0.5,  # size of the daily water wobble (kg)
        water_autocorr=0.7,  # how "sticky" the wobble is (0 = i.i.d., 0.9 = very sticky)
        spike_rate=0.05,  # chance per day of a big high-sodium spike
        missing_weight_rate=0.15,  # fraction of weigh-ins skipped
        missing_intake_rate=0.05,  # fraction of food logs skipped
        seed=42,  # same seed -> identical diary every run
    )
    obs, truth = res.observed, res.truth

    print("=== HIDDEN TRUTH (the answer key the estimator must recover) ===")
    print(f"  true TDEE      : {truth.true_tdee:.0f} kcal/day")
    print(f"  energy density : {truth.true_energy_density:.0f} kcal/kg")
    print(f"  clean weight   : {truth.true_weight[0]:.2f} -> {truth.true_weight[-1]:.2f} kg")
    print(f"  total loss     : {truth.true_weight[0] - truth.true_weight[-1]:.2f} kg")
    print()

    missed = int(np.isnan(obs.weight).sum())
    print(f"=== OBSERVED DIARY ({len(obs.day)} days, {missed} weigh-ins skipped) ===")
    print("  day | observed weight | intake | clean truth | noise")
    print("  ----+-----------------+--------+-------------+-------")
    for d in range(14):  # first two weeks
        clean = truth.true_weight[d]
        if np.isnan(obs.weight[d]):
            weight_col, noise_col = "    (missed)  ", "    --"
        else:
            weight_col = f"   {obs.weight[d]:7.2f} kg "
            noise_col = f"{obs.weight[d] - clean:+5.2f}"
        intake_col = "  --  " if np.isnan(obs.intake[d]) else f"{obs.intake[d]:5.0f} "
        print(f"  {d:3d} |{weight_col} | {intake_col} |  {clean:7.2f} kg | {noise_col}")
    print("  ... (106 more days)")
    print()
    print("Notice the observed column wobbles up and down (sometimes UP on a day the")
    print("truth is falling) — that water/glycogen noise is the signal-vs-noise problem")
    print("the estimator will have to see through in a later phase.")


if __name__ == "__main__":
    main()
