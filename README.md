# TrendCut

A self-hosted weight-and-calorie tracker that estimates your *actual* maintenance
calories (TDEE) from the observed relationship between what you eat and how your
weight moves, smooths daily noise into a stable trend, and tells you when a real
plateau has set in versus when you're just looking at water weight. The estimation
logic is deterministic and mathematically defined, which makes it unusually easy to
test — see [`PRD.md`](PRD.md) for the full design.

> **Status:** Phase 1 — the synthetic ground-truth generator (`estimator/synthetic.py`)
> is implemented and tested. The remaining `estimator` submodules (`smoothing`,
> `tdee`, `plateau`) are intentionally empty stubs; their logic arrives in later phases.
> See [`examples/simulate_demo.py`](examples/simulate_demo.py) for a runnable demo.

## Layout

```
estimator/      # pure, framework-free estimation engine (the unit under test)
  smoothing.py  # gap-aware EWMA trend weight
  tdee.py       # rolling regression + energy-balance TDEE estimate
  plateau.py    # graded plateau classifier
  synthetic.py  # synthetic ground-truth data generation for tests
examples/       # runnable demos (e.g. simulate_demo.py)
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
pytest                    # tests
```

These three are exactly what CI runs on every push and pull request (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
