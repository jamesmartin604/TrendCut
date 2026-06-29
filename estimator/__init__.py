"""TrendCut estimation engine.

A pure, deterministic, framework-free package that turns a noisy daily series of
body weight and calorie intake into a smoothed trend, a rolling TDEE estimate with
uncertainty, and a graded plateau classification.

This package performs no I/O and has no web/database coupling — it takes series in
and returns estimates out, which is what makes it the well-tested core of the
project (see the PRD's §7 "estimation engine" and §11 "testing strategy").

Phase 0: all submodules are intentionally empty stubs. Logic arrives in later
phases (M2 smoothing, M3 TDEE, M4 plateau).
"""
