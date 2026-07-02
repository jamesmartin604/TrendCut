"""Thin web + storage layer over the (pure) estimator package.

This package holds all framework/database concerns — FastAPI routing, SQLite
persistence, CSV import/export. The ``estimator`` package it consumes stays pure
and framework-free; nothing here leaks back into it.

Key rule (PRD §9.1): trend weight, TDEE, and plateau state are **derived, never
stored**. The database holds only raw daily ``entries`` (and ``settings``); every
``/estimate`` recomputes from those raw rows so editing history always yields a
consistent result.
"""
