"""Smoke test: the package imports and the suite runs green.

Replaced with real estimator tests in later phases (PRD §11).
"""

import estimator


def test_package_imports() -> None:
    assert estimator.__doc__ is not None
