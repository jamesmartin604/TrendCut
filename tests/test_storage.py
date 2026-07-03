"""Storage-layer tests (Phase 6).

These prove the SQLite persistence layer is correct *without* spinning up the HTTP
layer — the whole point of keeping :mod:`api.storage` framework-free. Every test
uses an isolated in-memory database (``Storage(":memory:")``), so there is no
shared state and no reliance on a real DB file.
"""

from __future__ import annotations

import threading

import pytest

from api.storage import Storage


@pytest.fixture
def store() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()


def test_upsert_and_get_roundtrip(store: Storage) -> None:
    entry = store.upsert_entry("2026-01-01", 80.5, 2000)
    assert entry.date == "2026-01-01"
    assert entry.weight_kg == 80.5
    assert entry.kcal == 2000
    assert entry.created_at and entry.updated_at

    fetched = store.get_entry("2026-01-01")
    assert fetched == entry


def test_get_missing_entry_returns_none(store: Storage) -> None:
    assert store.get_entry("2099-12-31") is None


def test_weight_only_and_calorie_only_days(store: Storage) -> None:
    weight_only = store.upsert_entry("2026-01-01", 80.0, None)
    assert weight_only.weight_kg == 80.0
    assert weight_only.kcal is None

    kcal_only = store.upsert_entry("2026-01-02", None, 1800)
    assert kcal_only.weight_kg is None
    assert kcal_only.kcal == 1800


def test_upsert_overwrites_and_preserves_created_at(store: Storage) -> None:
    first = store.upsert_entry("2026-01-01", 80.0, 2000)
    second = store.upsert_entry("2026-01-01", 79.5, 1900)

    # Re-logging a date overwrites in place — never a second row.
    assert len(store.list_entries()) == 1
    assert second.weight_kg == 79.5
    assert second.kcal == 1900
    # created_at is preserved across the upsert; updated_at moves (weakly, since the
    # clock may tick within a test — assert it did not go backwards).
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


def test_list_entries_ordered_and_range_filtered(store: Storage) -> None:
    store.upsert_entry("2026-01-03", 79.0, None)
    store.upsert_entry("2026-01-01", 80.0, None)
    store.upsert_entry("2026-01-02", 79.5, None)

    all_dates = [e.date for e in store.list_entries()]
    assert all_dates == ["2026-01-01", "2026-01-02", "2026-01-03"]

    ranged = [e.date for e in store.list_entries("2026-01-02", "2026-01-03")]
    assert ranged == ["2026-01-02", "2026-01-03"]


def test_delete_entry(store: Storage) -> None:
    store.upsert_entry("2026-01-01", 80.0, 2000)
    assert store.delete_entry("2026-01-01") is True
    assert store.get_entry("2026-01-01") is None
    # Deleting a non-existent date reports False rather than raising.
    assert store.delete_entry("2026-01-01") is False


def test_settings_defaults_and_partial_update(store: Storage) -> None:
    defaults = store.get_settings()
    assert defaults.alpha == 0.10
    assert defaults.window_days == 28
    assert defaults.energy_density == 7700.0
    assert defaults.units == "kg"

    updated = store.update_settings(window_days=14, alpha=0.2)
    assert updated.window_days == 14
    assert updated.alpha == 0.2
    # Untouched fields keep their previous values.
    assert updated.energy_density == 7700.0

    # A None value is ignored (partial update), unknown keys are dropped.
    again = store.update_settings(alpha=None, bogus="x")  # type: ignore[arg-type]
    assert again.alpha == 0.2


def test_settings_persist_across_reads(store: Storage) -> None:
    store.update_settings(target_weight=75.0, target_date="2026-08-31")
    s = store.get_settings()
    assert s.target_weight == 75.0
    assert s.target_date == "2026-08-31"


def test_concurrent_reads_are_safe(store: Storage) -> None:
    """Regression: FastAPI serves sync handlers from a threadpool, so reads hit the
    shared connection concurrently (e.g. the dashboard fires /estimate and /settings
    in parallel). Unlocked reads corrupted each other's fetches (wrong-shape/None
    rows); all reads must therefore hold the storage lock."""
    for i in range(1, 29):
        store.upsert_entry(f"2026-01-{i:02d}", 80.0 - i * 0.05, 2000)

    errors: list[Exception] = []

    def hammer(fn) -> None:
        try:
            for _ in range(2000):
                fn()
        except Exception as exc:  # noqa: BLE001 - any corruption error fails the test
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(store.get_settings,)),
        threading.Thread(target=hammer, args=(store.list_entries,)),
        threading.Thread(target=hammer, args=(lambda: store.get_entry("2026-01-05"),)),
        threading.Thread(target=hammer, args=(store.get_settings,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
