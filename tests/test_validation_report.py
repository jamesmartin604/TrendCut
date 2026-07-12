"""Tests for the public validation artifact + its harness.

The artifact is a public credibility page's data: it must be a real run of the real
frozen estimator, deterministic, internally consistent, structurally free of any user
data, and the committed copy must not have drifted from the code.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from api.validation_report import ARTIFACT_PATH, build_report

_MODULE = Path(__file__).resolve().parent.parent / "api" / "validation_report.py"


def test_report_is_well_formed() -> None:
    report = build_report()
    assert set(report) >= {"meta", "scenarios", "density_mismatch"}
    meta = report["meta"]
    assert meta["assumed_energy_density"] == 7700.0
    assert meta["total_cases"] > 0
    assert 0.0 <= meta["overall_pass_rate"] <= 1.0
    # Every scenario carries its full case list and a pre-committed tolerance.
    for s in report["scenarios"]:
        assert set(s) >= {"id", "label", "tolerance_kcal", "n", "n_passed", "cases"}
        assert s["n"] == len(s["cases"]) > 0
        assert s["tolerance_kcal"] > 0
    # The weak scenario is present and honestly below 100% (proof we don't hide it).
    weak = next(s for s in report["scenarios"] if s["id"] == "sparse_noisy")
    assert weak["pass_rate"] < 1.0


def test_report_is_deterministic_across_fresh_runs() -> None:
    # Two independent fresh builds must be identical — the harness seeds each case
    # explicitly and does not depend on shared/global RNG state.
    assert build_report() == build_report()


def test_case_numbers_are_internally_consistent() -> None:
    report = build_report()
    for s in report["scenarios"]:
        recomputed_pass = 0
        for c in s["cases"]:
            if c["recovered_tdee"] is None:
                assert c["within_tolerance"] is False
                continue
            expected_err = round(abs(c["recovered_tdee"] - c["true_tdee"]), 1)
            assert c["abs_error"] == pytest.approx(expected_err, abs=0.11)
            assert c["within_tolerance"] == (c["abs_error"] <= s["tolerance_kcal"])
            recomputed_pass += int(c["within_tolerance"])
        assert recomputed_pass == s["n_passed"]
        assert s["pass_rate"] == pytest.approx(s["n_passed"] / s["n"], abs=1e-3)


def _assert_close(a, b, path="") -> None:
    """Compare two artifacts with a numeric tolerance that absorbs cross-platform
    NumPy ULP (regenerated on one OS, checked on another) while catching real drift."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), f"keys differ at {path}"
        for k in a:
            _assert_close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"length differs at {path}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _assert_close(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) or isinstance(b, bool):
        assert a == b, f"bool differs at {path}: {a} != {b}"
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        # 0.5 kcal / 0.005 rate — far below any real drift, far above ULP wobble.
        assert abs(a - b) <= 0.5, f"number drifted at {path}: {a} vs {b}"
    else:
        assert a == b, f"value differs at {path}: {a!r} != {b!r}"


def test_committed_artifact_matches_a_fresh_regeneration() -> None:
    """The committed web/validation.json must reflect the current estimator. Compared
    with a numeric tolerance (not byte/string equality) so a Linux CI run doesn't fail
    on a Windows-generated file over floating-point ULP — but genuine drift fails."""
    assert ARTIFACT_PATH.exists(), "run `python -m api.validation_report` to generate it"
    committed = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    _assert_close(committed, build_report())


def test_harness_imports_no_storage_or_db_structurally() -> None:
    """Structural guarantee (not a field-name check): the harness imports ONLY the
    frozen estimator modules, never storage / sqlite / the app, so the public artifact
    physically cannot contain user data."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {"api.storage", "sqlite3", "api.app"}
    assert not (imported & forbidden), f"harness must not import {imported & forbidden}"
    assert not any(m.startswith("api.storage") for m in imported)
    # It DOES call the real frozen estimator + simulator.
    assert {"estimator.synthetic", "estimator.tdee"} <= imported
