"""Synthetic ground-truth data generation.

This module builds a daily intake/weight series from a *known* hidden TDEE via the
energy-balance model, then corrupts the clean weight trajectory with realistic
observation noise. Every later estimator test depends on this generator, so the
guiding principle here is *honesty*: the simulator must be able to disagree with
whatever the future estimator assumes, otherwise the recovery tests in Phase 3
would be circular and prove nothing.

This module **generates** data only. It never tries to recover the hidden TDEE,
and it deliberately does not import from ``smoothing``/``tdee``/``plateau`` (PRD
§11.1).

The model
---------
Energy balance (PRD §5.1). For each day ``d``::

    surplus[d]        = intake[d] - true_tdee                 # kcal
    weight_change[d]  = surplus[d] / true_energy_density       # kg

The *true* (clean) morning weight on day ``d`` is the start weight plus every
prior day's change::

    true_weight[d] = start_weight + sum(weight_change[k] for k < d)

so ``true_weight[0] == start_weight`` and a steady deficit produces a straight,
downward-sloping line — the analytic expectation the zero-noise test pins down.

The *observed* weight the estimator would actually see is the truth plus noise::

    observed_weight[d] = true_weight[d] + water[d] + spike[d]

where the noise terms model the real confounders that swamp the fat-loss signal
(PRD §5.2):

``water`` — **autocorrelated** water/glycogen noise, generated as an AR(1)
process rather than i.i.d. Gaussian. Real water retention persists for several
days, and that persistence is exactly what breaks naive day-over-day estimators,
so it must be in the ground truth. Parameterised by its stationary standard
deviation (``water_sd``) and its lag-1 autocorrelation (``water_autocorr``).

``spike`` — occasional larger upward bumps modelling a high-sodium meal or refeed
(positive water retention), occurring on a configurable fraction of days
(``spike_rate``) with a configurable scale (``spike_magnitude``).

Finally, real logs are sparse: weight and intake entries are *independently*
dropped to missing (``NaN``) at configurable rates, since a user might weigh in
without finishing their food log, or vice versa (PRD FR-2).

Why ``true_energy_density`` is a parameter
------------------------------------------
The estimator will assume some fixed energy-density constant (commonly
~7700 kcal/kg). If the simulator hardcoded the same value, recovery tests would
only ever confirm the estimator against its own assumption. Keeping it a
parameter lets a test drive the data with, say, 8200 kcal/kg and check the
estimator still behaves — i.e. it exposes model mismatch instead of hiding it.

Everything is reproducible from ``seed``.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

__all__ = ["Observed", "Truth", "SimulationResult", "simulate"]

# Default energy density of body-mass change (kcal per kg). A deliberate
# simplification — real change mixes fat, lean tissue, and water (PRD §5.1).
DEFAULT_ENERGY_DENSITY = 7700.0

IntakeSpec = float | Sequence[float] | Callable[[int], float] | np.ndarray


@dataclass(frozen=True, eq=False)
class Observed:
    """The data the estimator actually sees — corrupted and gappy.

    Arrays are aligned on ``day`` and all have length ``n_days``. Missing entries
    are represented as ``NaN``.
    """

    day: np.ndarray  # integer day index 0..n_days-1
    weight: np.ndarray  # observed body weight (kg), NaN where missing
    intake: np.ndarray  # observed intake (kcal), NaN where missing
    dates: np.ndarray | None  # datetime64[D] calendar dates, or None


@dataclass(frozen=True, eq=False)
class Truth:
    """The hidden ground truth — what the estimator is trying to recover.

    Kept strictly separate from :class:`Observed` so a test can never
    accidentally feed truth into an estimator.
    """

    true_tdee: float  # the hidden maintenance calories (kcal/day)
    true_energy_density: float  # kcal per kg used to drive the trajectory
    start_weight: float  # weight on day 0 (kg)
    intake: np.ndarray  # realised daily intake that drove the weight (kcal), no gaps
    daily_weight_change: np.ndarray  # per-day change (kg) = surplus / energy_density
    true_weight: np.ndarray  # clean noise-free weight trajectory (kg), no gaps


@dataclass(frozen=True, eq=False)
class SimulationResult:
    """Bundle of :class:`Observed` data and its :class:`Truth`."""

    observed: Observed
    truth: Truth


def _resolve_intake(intake: IntakeSpec, n_days: int) -> np.ndarray:
    """Turn the flexible intake spec into a clean float array of length ``n_days``.

    Accepts a constant (steady intake), a callable ``d -> kcal`` (e.g. a refeed
    schedule), or an explicit per-day sequence/array.
    """
    if callable(intake):
        values = np.fromiter((float(intake(d)) for d in range(n_days)), dtype=float, count=n_days)
    elif np.isscalar(intake):
        values = np.full(n_days, float(intake))  # type: ignore[arg-type]
    else:
        values = np.asarray(intake, dtype=float)
        if values.ndim != 1 or values.shape[0] != n_days:
            raise ValueError(
                f"intake sequence must have length n_days={n_days}, got shape {values.shape}"
            )
    if not np.all(np.isfinite(values)):
        raise ValueError("intake must be finite for every day")
    return values


def _ar1_noise(rng: np.random.Generator, n_days: int, sd: float, phi: float) -> np.ndarray:
    """Generate an AR(1) noise series with stationary std ``sd`` and lag-1 corr ``phi``.

    Initialised from the stationary distribution so the series is autocorrelated
    from day 0 (no burn-in transient). With ``phi == 0`` this degenerates to i.i.d.
    Gaussian noise, which is exactly the (wrong) assumption we want to be able to
    contrast against.
    """
    if sd == 0.0:
        return np.zeros(n_days)
    # innovation variance chosen so the marginal/stationary std equals `sd`.
    eps_sd = sd * np.sqrt(1.0 - phi * phi)
    out = np.empty(n_days)
    out[0] = rng.normal(0.0, sd)  # draw from the stationary distribution
    for d in range(1, n_days):
        out[d] = phi * out[d - 1] + rng.normal(0.0, eps_sd)
    return out


def _spikes(rng: np.random.Generator, n_days: int, rate: float, magnitude: float) -> np.ndarray:
    """Occasional positive water-retention bumps (high-sodium/refeed days)."""
    if rate == 0.0 or magnitude == 0.0:
        return np.zeros(n_days)
    occurs = rng.random(n_days) < rate
    draws = np.abs(rng.normal(0.0, magnitude, size=n_days))  # upward bumps only
    return np.where(occurs, draws, 0.0)


def simulate(
    n_days: int,
    *,
    true_tdee: float = 2500.0,
    intake: IntakeSpec = 2000.0,
    start_weight: float = 80.0,
    true_energy_density: float = DEFAULT_ENERGY_DENSITY,
    water_sd: float = 0.5,
    water_autocorr: float = 0.7,
    spike_rate: float = 0.03,
    spike_magnitude: float = 1.0,
    missing_weight_rate: float = 0.0,
    missing_intake_rate: float = 0.0,
    seed: int | None = None,
    start_date: _dt.date | None = None,
) -> SimulationResult:
    """Simulate a daily intake/weight log from a known hidden TDEE.

    Parameters
    ----------
    n_days:
        Number of consecutive days to generate (>= 1).
    true_tdee:
        The hidden maintenance calories (kcal/day) the estimator must recover.
    intake:
        Daily intake (kcal). A constant, a per-day sequence of length ``n_days``,
        or a callable ``day_index -> kcal`` (model steady deficits and refeeds).
    start_weight:
        Body weight (kg) on day 0.
    true_energy_density:
        kcal per kg of body-mass change used to drive the trajectory. **Set this
        different from the estimator's assumed constant to test model mismatch.**
    water_sd:
        Stationary standard deviation (kg) of the autocorrelated water-weight
        noise. ``0`` disables water noise.
    water_autocorr:
        Lag-1 autocorrelation (phi) of the AR(1) water noise; must be in
        ``(-1, 1)``. ``0`` makes the noise i.i.d. Defaults to a persistent 0.7.
    spike_rate:
        Probability per day of a larger upward water-retention spike.
    spike_magnitude:
        Scale (kg) of spike bumps (std of the half-normal draw).
    missing_weight_rate, missing_intake_rate:
        Independent probabilities of dropping that day's weight / intake to
        ``NaN`` (sparse logging).
    seed:
        Seed for reproducibility. Same seed -> identical output.
    start_date:
        Optional calendar date for day 0; if given, ``observed.dates`` is
        populated with consecutive ``datetime64[D]`` values.

    Returns
    -------
    SimulationResult
        ``.observed`` (gappy, noisy) and ``.truth`` (clean, complete).
    """
    if n_days < 1:
        raise ValueError(f"n_days must be >= 1, got {n_days}")
    if true_energy_density <= 0.0:
        raise ValueError(f"true_energy_density must be > 0, got {true_energy_density}")
    if not -1.0 < water_autocorr < 1.0:
        raise ValueError(f"water_autocorr must be in (-1, 1), got {water_autocorr}")
    if water_sd < 0.0 or spike_magnitude < 0.0:
        raise ValueError("water_sd and spike_magnitude must be non-negative")
    for name, rate in (
        ("spike_rate", spike_rate),
        ("missing_weight_rate", missing_weight_rate),
        ("missing_intake_rate", missing_intake_rate),
    ):
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"{name} must be in [0, 1], got {rate}")

    rng = np.random.default_rng(seed)

    # --- the clean energy-balance trajectory (the hidden truth) ---------------
    intake_arr = _resolve_intake(intake, n_days)
    daily_weight_change = (intake_arr - true_tdee) / true_energy_density
    # true_weight[d] = start + sum of changes strictly before day d.
    cumulative = np.concatenate(([0.0], np.cumsum(daily_weight_change)[:-1]))
    true_weight = start_weight + cumulative

    # --- observation noise (draw order fixed for reproducibility) -------------
    water = _ar1_noise(rng, n_days, water_sd, water_autocorr)
    spike = _spikes(rng, n_days, spike_rate, spike_magnitude)
    observed_weight = true_weight + water + spike

    # --- independent missing-data masks ---------------------------------------
    observed_intake = intake_arr.copy()
    drop_weight = rng.random(n_days) < missing_weight_rate
    drop_intake = rng.random(n_days) < missing_intake_rate
    observed_weight = np.where(drop_weight, np.nan, observed_weight)
    observed_intake = np.where(drop_intake, np.nan, observed_intake)

    day = np.arange(n_days)
    dates: np.ndarray | None = None
    if start_date is not None:
        dates = np.datetime64(start_date, "D") + day.astype("timedelta64[D]")

    observed = Observed(
        day=day,
        weight=observed_weight,
        intake=observed_intake,
        dates=dates,
    )
    truth = Truth(
        true_tdee=float(true_tdee),
        true_energy_density=float(true_energy_density),
        start_weight=float(start_weight),
        intake=intake_arr,
        daily_weight_change=daily_weight_change,
        true_weight=true_weight,
    )
    return SimulationResult(observed=observed, truth=truth)
