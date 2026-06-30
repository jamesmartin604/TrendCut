"""Time-series smoothing.

Gap-aware exponentially weighted moving average (EWMA) of body weight — the
"trend weight" line that strips day-to-day water/glycogen noise out of the raw
scale signal (PRD §6.2, §7.1 step 2). Implemented from scratch (no pandas
``ewm``) so the weighting is fully under our control — gap-awareness in
particular is not something the off-the-shelf smoothers get right.

The recurrence
--------------
For a gap-free daily series the trend follows the classic EWMA recurrence::

    trend[0] = weight[0]                       # seed with the first real value
    trend[t] = (1 - alpha) * trend[t-1] + alpha * weight[t]

``alpha`` in ``(0, 1]`` is the smoothing factor: larger ``alpha`` tracks the raw
signal more closely (less smoothing, less lag); smaller ``alpha`` smooths harder
and lags more. ``alpha == 1`` disables smoothing (trend == raw).

alpha <-> effective window
--------------------------
Each past observation's weight in the average decays geometrically as
``(1 - alpha)**j`` with age ``j`` days. Two common ways to read that as a window:

* **Center of mass** (mean age of the weighting), ``(1 - alpha) / alpha`` days —
  the rigorous "effective window". At the default ``alpha = 0.10`` that is 9 days.
* The looser rule of thumb ``1 / alpha`` days, i.e. the ~10-day window the PRD
  refers to for ``alpha ~= 0.10`` (PRD §6.2 / §7.2).

:func:`effective_window` returns the center-of-mass value.

Gap-awareness
-------------
Real logs skip days, and the simulator emits those gaps as ``NaN`` (PRD FR-2,
FR-8). A naive EWMA that applies ``alpha`` once per array slot would treat a
weigh-in 7 days later as if it were the very next day, badly under-smoothing
across gaps. Instead the weighting depends on elapsed **time**, not array
position: when a real observation arrives ``k`` days after the previous real one,
the previous trend's influence is decayed as if ``alpha`` had been applied ``k``
times::

    decay   = (1 - alpha) ** k
    trend   = decay * prev_trend + (1 - decay) * weight

For ``k == 1`` this is exactly the classic recurrence above. A ``k``-day gap with
new value ``w`` is therefore identical to observing ``w`` on each of the ``k``
intervening days — gap-aware smoothing makes no assumption beyond "the next real
value is the next thing we know".

Behaviour at the edges (all documented choices, not accidents):

* **First observation** seeds the trend with that raw value (``trend = weight``).
* **Leading gaps** (``NaN`` before the first real weigh-in) have no trend yet, so
  the output is ``NaN`` for those days.
* **Missing days after the trend exists** carry the last trend forward unchanged
  (the reported trend is flat across a gap) — we have no new information on those
  days, so the line holds rather than inventing movement or going ``NaN``. The
  next real observation then decays against the elapsed gap as above.
* **All-missing input** returns an all-``NaN`` series of the same length.

The output is always the same length as the input and aligned day-for-day.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike

__all__ = ["DEFAULT_ALPHA", "effective_window", "ewma_trend"]

# ~10-day effective window in the Hacker's Diet / TrendWeight lineage (PRD §7.2).
DEFAULT_ALPHA = 0.10


def effective_window(alpha: float) -> float:
    """Center-of-mass (mean age, in days) of the EWMA weighting for ``alpha``.

    ``(1 - alpha) / alpha`` — the rigorous reading of "effective window". At the
    default ``alpha = 0.10`` this is 9 days (the PRD's loose "~10-day" figure is
    the ``1 / alpha`` rule of thumb).
    """
    if not 0.0 < alpha <= 1.0:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")
    return (1.0 - alpha) / alpha


def ewma_trend(weights: ArrayLike, alpha: float = DEFAULT_ALPHA) -> np.ndarray:
    """Gap-aware EWMA "trend weight" of a daily body-weight series.

    Parameters
    ----------
    weights:
        Daily body weight, one entry per day. Missing days are ``NaN`` (or
        ``None``); array position is the day index, so elapsed time between two
        real observations is the difference of their positions.
    alpha:
        Smoothing factor in ``(0, 1]``. Default :data:`DEFAULT_ALPHA` (0.10).
        See the module docstring for the alpha <-> effective-window relationship.

    Returns
    -------
    numpy.ndarray
        The trend series, same length as ``weights`` and aligned day-for-day.
        ``NaN`` only for leading days before the first real observation (or for
        an all-missing input). See the module docstring for the full behaviour at
        gaps, the first observation, and missing days.
    """
    if not 0.0 < alpha <= 1.0:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")

    raw = np.asarray(weights, dtype=float)
    if raw.ndim != 1:
        raise ValueError(f"weights must be 1-dimensional, got shape {raw.shape}")

    trend = np.full(raw.shape[0], np.nan)
    current: float | None = None  # current trend value, None until first real obs
    last_obs_idx: int | None = None  # array index of the most recent real obs

    one_minus_alpha = 1.0 - alpha
    for i, w in enumerate(raw):
        if np.isnan(w):
            if current is not None:
                trend[i] = current  # carry the trend forward across the gap
            continue
        if current is None:
            current = float(w)  # seed with the first real value
        else:
            k = i - last_obs_idx  # elapsed days since the previous real obs
            decay = one_minus_alpha**k
            current = decay * current + (1.0 - decay) * float(w)
        trend[i] = current
        last_obs_idx = i

    return trend
