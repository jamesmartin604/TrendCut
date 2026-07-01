# TrendCut

A self-hosted weight-and-calorie tracker that estimates your *actual* maintenance
calories (TDEE) from the observed relationship between what you eat and how your
weight moves, smooths daily noise into a stable trend, and tells you when a real
plateau has set in versus when you're just looking at water weight. The estimation
logic is deterministic and mathematically defined, which makes it unusually easy to
test — see [`PRD.md`](PRD.md) for the full design.

> **Status:** Phase 5 — the estimation core is complete. The synthetic
> ground-truth generator (`estimator/synthetic.py`), the gap-aware EWMA trend
> smoother (`estimator/smoothing.py`), the rolling-regression TDEE estimator
> (`estimator/tdee.py`), and the graded plateau classifier
> (`estimator/plateau.py`) are all implemented and tested. The estimator recovers a
> known TDEE from noisy synthetic data within ±100 kcal (matched energy density,
> moderate noise), a `hypothesis` property-based suite (`tests/test_properties.py`)
> fuzzes its robustness invariants, and the plateau classifier distinguishes a
> genuine stall from a water-weight swing. Remaining work is plumbing (API, storage,
> frontend, deploy). See [`examples/`](examples/) for runnable demos —
> [`simulate_demo.py`](examples/simulate_demo.py) generates a noisy diary,
> [`smoothing_demo.py`](examples/smoothing_demo.py) charts the trend line through it,
> [`tdee_demo.py`](examples/tdee_demo.py) recovers the hidden TDEE, and
> [`plateau_demo.py`](examples/plateau_demo.py) tells a stall apart from a swing.

## Layout

```
estimator/      # pure, framework-free estimation engine (the unit under test)
  smoothing.py  # gap-aware EWMA trend weight
  tdee.py       # rolling regression + energy-balance TDEE estimate
  plateau.py    # graded plateau classifier
  synthetic.py  # synthetic ground-truth data generation for tests
examples/       # runnable demos (simulate/smoothing/tdee/plateau _demo.py)
tests/          # pytest + hypothesis suite
```

## Development setup

Requires Python 3.12+.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1

# 2. Install the package with its dev dependencies
pip install -e ".[dev]"
```

## Checks

```bash
ruff check .              # lint
ruff format --check .     # formatting check
pytest                    # tests (property tests use the fast "ci" hypothesis profile)
```

For heavier local fuzzing, run the property suite under the deep profile
(2500 examples/test instead of 150) via the `HYPOTHESIS_PROFILE` env var — in
PowerShell:

```powershell
$env:HYPOTHESIS_PROFILE="deep"; pytest tests/test_properties.py -q; Remove-Item Env:HYPOTHESIS_PROFILE
```

These three are exactly what CI runs on every push and pull request (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
