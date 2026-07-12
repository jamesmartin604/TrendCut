"""Generate the public validation artifact — the estimator's accuracy on synthetic
ground truth, from a real run of the REAL frozen estimator + simulator.

This is a thin harness: it imports and calls the same frozen code the tests use
(:func:`estimator.synthetic.simulate` and :func:`estimator.tdee.estimate_tdee`) and
records structured results. It **must not** import storage / the DB / any user data —
the artifact it produces is served on a public page, so it is synthetic-only by
construction (a test asserts this module imports nothing from ``api.storage`` etc.).

Numbers are deterministic (fixed per-case seeds) and rounded coarser than any
plausible cross-platform floating-point wobble, so the committed
``web/validation.json`` can be checked against a fresh regeneration.

Regenerate with::

    python -m api.validation_report        # writes web/validation.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from estimator.synthetic import simulate
from estimator.tdee import DEFAULT_ASSUMED_ENERGY_DENSITY, estimate_tdee

__all__ = ["ARTIFACT_PATH", "build_report", "write_report"]

# web/validation.json — a static asset served publicly and baked into the image.
ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "web" / "validation.json"

# Matrix axes shared by the pass/fail scenarios.
_TDEES = (2200.0, 2500.0, 2800.0)
_DEFICITS = (300.0, 500.0, 750.0)
_SEEDS = tuple(range(5))
# Ample EWMA warm-up before the regression window (mirrors the test harness).
_WARMUP_DAYS = 40
_START_WEIGHT = 85.0

# Rounding coarse enough to absorb cross-platform NumPy ULP differences (we
# regenerate on one OS, CI checks currency on another) while still exposing any real
# drift in the estimator's numbers.
_KCAL_DP = 1
_RATE_DP = 3


@dataclass(frozen=True)
class _Scenario:
    id: str
    label: str
    description: str
    window: int
    water_sd: float
    autocorr: float
    spike: float
    missing: float
    tolerance_kcal: float  # a PRE-COMMITTED accuracy bar — not tuned to pass


# The published scenarios. Tolerances are fixed accuracy claims; the report records
# the REAL pass rate against them, including sub-100%. The sparse/noisy case is the
# honest weak spot and is expected to degrade.
_SCENARIOS = (
    _Scenario(
        id="headline",
        label="Headline — clean-ish cut",
        description=(
            "Matched energy density, moderate autocorrelated water noise, a 28-day "
            "window and complete logging. The go/no-go case."
        ),
        window=28,
        water_sd=0.4,
        autocorr=0.6,
        spike=0.0,
        missing=0.0,
        tolerance_kcal=100.0,
    ),
    _Scenario(
        id="robust",
        label="Robust — noise, spikes & gaps",
        description=(
            "Stronger AR(1) water noise, occasional refeed spikes and ~10% missing "
            "days, over a longer 42-day window."
        ),
        window=42,
        water_sd=0.6,
        autocorr=0.7,
        spike=0.05,
        missing=0.1,
        tolerance_kcal=150.0,
    ),
    _Scenario(
        id="sparse_noisy",
        label="Sparse & noisy — the weak spot",
        description=(
            "Deliberately hard: heavy water noise, frequent spikes, ~30% missing "
            "weigh-ins and a short 21-day window. Where the estimator is weakest — "
            "shown honestly, not hidden."
        ),
        window=21,
        water_sd=0.9,
        autocorr=0.7,
        spike=0.08,
        missing=0.3,
        tolerance_kcal=250.0,
    ),
)

# Anti-circularity demonstration: drive the data with an energy density DIFFERENT
# from the estimator's assumed constant and show the resulting bias is the known
# physical amount — proof the recovery tests aren't circular.
_MISMATCH_TDEE = 2500.0
_MISMATCH_DEFICIT = 600.0
_MISMATCH_WINDOW = 42
_MISMATCH_SEEDS = tuple(range(10))
_MISMATCH_DENSITIES = (7000.0, 8400.0)


def _r(value: float, dp: int) -> float:
    return round(float(value), dp)


def _run(
    *,
    true_tdee: float,
    deficit: float,
    seed: int,
    window: int,
    water_sd: float,
    autocorr: float,
    spike: float,
    missing: float,
    true_density: float,
):
    res = simulate(
        n_days=window + _WARMUP_DAYS,
        true_tdee=true_tdee,
        intake=true_tdee - deficit,
        start_weight=_START_WEIGHT,
        true_energy_density=true_density,
        water_sd=water_sd,
        water_autocorr=autocorr,
        spike_rate=spike,
        missing_weight_rate=missing,
        missing_intake_rate=0.0,
        seed=seed,
    )
    return estimate_tdee(res.observed.weight, res.observed.intake, window_days=window)


def _scenario_report(scn: _Scenario) -> dict:
    cases: list[dict] = []
    passes = 0
    abs_errors: list[float] = []
    for true_tdee in _TDEES:
        for deficit in _DEFICITS:
            for seed in _SEEDS:
                est = _run(
                    true_tdee=true_tdee,
                    deficit=deficit,
                    seed=seed,
                    window=scn.window,
                    water_sd=scn.water_sd,
                    autocorr=scn.autocorr,
                    spike=scn.spike,
                    missing=scn.missing,
                    true_density=DEFAULT_ASSUMED_ENERGY_DENSITY,
                )
                recovered = est.tdee
                if recovered is None:
                    cases.append(
                        {
                            "true_tdee": true_tdee,
                            "deficit": deficit,
                            "seed": seed,
                            "recovered_tdee": None,
                            "abs_error": None,
                            "within_tolerance": False,
                            "status": est.status,
                        }
                    )
                    continue
                abs_error = abs(recovered - true_tdee)
                within = abs_error <= scn.tolerance_kcal
                passes += int(within)
                abs_errors.append(abs_error)
                cases.append(
                    {
                        "true_tdee": true_tdee,
                        "deficit": deficit,
                        "seed": seed,
                        "recovered_tdee": _r(recovered, _KCAL_DP),
                        "abs_error": _r(abs_error, _KCAL_DP),
                        "within_tolerance": within,
                        "status": est.status,
                    }
                )
    n = len(cases)
    return {
        "id": scn.id,
        "label": scn.label,
        "description": scn.description,
        "params": {
            "window_days": scn.window,
            "water_sd": scn.water_sd,
            "water_autocorr": scn.autocorr,
            "spike_rate": scn.spike,
            "missing_weight_rate": scn.missing,
        },
        "tolerance_kcal": scn.tolerance_kcal,
        "n": n,
        "n_passed": passes,
        "pass_rate": _r(passes / n, _RATE_DP) if n else 0.0,
        "mean_abs_error": _r(sum(abs_errors) / len(abs_errors), _KCAL_DP) if abs_errors else None,
        "max_abs_error": _r(max(abs_errors), _KCAL_DP) if abs_errors else None,
        "cases": cases,
    }


def _density_mismatch_report() -> dict:
    """Recover under a deliberate density mismatch; show the bias equals the known
    closed form ``deficit*(assumed-true)/true`` — i.e. the recovery isn't circular."""
    groups = []
    for true_density in _MISMATCH_DENSITIES:
        expected_bias = (
            _MISMATCH_DEFICIT * (DEFAULT_ASSUMED_ENERGY_DENSITY - true_density) / true_density
        )
        biases = []
        for seed in _MISMATCH_SEEDS:
            est = _run(
                true_tdee=_MISMATCH_TDEE,
                deficit=_MISMATCH_DEFICIT,
                seed=seed,
                window=_MISMATCH_WINDOW,
                water_sd=0.2,
                autocorr=0.5,
                spike=0.0,
                missing=0.0,
                true_density=true_density,
            )
            biases.append(est.tdee - _MISMATCH_TDEE)
        groups.append(
            {
                "true_energy_density": true_density,
                "assumed_energy_density": DEFAULT_ASSUMED_ENERGY_DENSITY,
                "expected_bias_kcal": _r(expected_bias, _KCAL_DP),
                "observed_mean_bias_kcal": _r(sum(biases) / len(biases), _KCAL_DP),
                "n": len(biases),
            }
        )
    return {
        "description": (
            "The simulator's true energy density is set DIFFERENT from the estimator's "
            "assumed 7,700 kcal/kg. The estimate is then biased by exactly the known "
            "physical amount deficit×(assumed−true)/true — not a bug, and proof the "
            "recovery tests can't be circular (the estimator is never handed its own "
            "assumption as truth)."
        ),
        "deficit_kcal": _MISMATCH_DEFICIT,
        "groups": groups,
    }


def build_report() -> dict:
    """Run the full seeded matrix and return the structured artifact (deterministic)."""
    scenarios = [_scenario_report(scn) for scn in _SCENARIOS]
    total = sum(s["n"] for s in scenarios)
    passed = sum(s["n_passed"] for s in scenarios)
    return {
        "meta": {
            "generator": "api.validation_report",
            "assumed_energy_density": DEFAULT_ASSUMED_ENERGY_DENSITY,
            "start_weight": _START_WEIGHT,
            "warmup_days": _WARMUP_DAYS,
            "seeds_per_cell": len(_SEEDS),
            "total_cases": total,
            "total_passed": passed,
            "overall_pass_rate": _r(passed / total, _RATE_DP) if total else 0.0,
            "anti_circularity": (
                "The synthetic simulator's true energy density is kept separate from "
                "the estimator's assumed constant, so recovery is tested against "
                "independent ground truth — never the estimator's own assumption."
            ),
            "reproducibility": (
                "Deterministic from fixed per-case seeds. Regenerate with "
                "`python -m api.validation_report`; the committed artifact is checked "
                "against a fresh run in CI so it can't drift from the estimator."
            ),
        },
        "scenarios": scenarios,
        "density_mismatch": _density_mismatch_report(),
    }


def write_report(path: Path = ARTIFACT_PATH) -> Path:
    path.write_text(json.dumps(build_report(), indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    path = write_report()
    report = build_report()
    print(f"Wrote {path}")
    print(
        f"  {report['meta']['total_cases']} cases, overall pass rate "
        f"{report['meta']['overall_pass_rate']:.1%}"
    )
    for s in report["scenarios"]:
        print(
            f"  - {s['label']}: {s['n_passed']}/{s['n']} within ±{s['tolerance_kcal']:.0f} "
            f"(max err {s['max_abs_error']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
