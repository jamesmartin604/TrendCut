"""Storage-layer tests.

These prove the SQLite persistence layer is correct *without* spinning up the HTTP
layer — the whole point of keeping :mod:`api.storage` framework-free. Every test
uses an isolated in-memory database (``Storage(":memory:")``), so there is no
shared state and no reliance on a real DB file.

The storage layer is user-scoped (Phase 1 multi-user readiness): every method
takes an explicit ``user_id``. Single-user tests run as ``DEFAULT_USER_ID``; the
isolation and migration tests exercise the multi-tenant guarantees directly.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from api.storage import DEFAULT_USER_ID as U
from api.storage import Storage


@pytest.fixture
def store() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()


def test_upsert_and_get_roundtrip(store: Storage) -> None:
    entry = store.upsert_entry(U, "2026-01-01", 80.5, 2000)
    assert entry.date == "2026-01-01"
    assert entry.weight_kg == 80.5
    assert entry.kcal == 2000
    assert entry.created_at and entry.updated_at

    fetched = store.get_entry(U, "2026-01-01")
    assert fetched == entry


def test_get_missing_entry_returns_none(store: Storage) -> None:
    assert store.get_entry(U, "2099-12-31") is None


def test_weight_only_and_calorie_only_days(store: Storage) -> None:
    weight_only = store.upsert_entry(U, "2026-01-01", 80.0, None)
    assert weight_only.weight_kg == 80.0
    assert weight_only.kcal is None

    kcal_only = store.upsert_entry(U, "2026-01-02", None, 1800)
    assert kcal_only.weight_kg is None
    assert kcal_only.kcal == 1800


def test_upsert_overwrites_and_preserves_created_at(store: Storage) -> None:
    first = store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    second = store.upsert_entry(U, "2026-01-01", 79.5, 1900)

    # Re-logging a date overwrites in place — never a second row.
    assert len(store.list_entries(U)) == 1
    assert second.weight_kg == 79.5
    assert second.kcal == 1900
    # created_at is preserved across the upsert; updated_at moves (weakly, since the
    # clock may tick within a test — assert it did not go backwards).
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


def test_list_entries_ordered_and_range_filtered(store: Storage) -> None:
    store.upsert_entry(U, "2026-01-03", 79.0, None)
    store.upsert_entry(U, "2026-01-01", 80.0, None)
    store.upsert_entry(U, "2026-01-02", 79.5, None)

    all_dates = [e.date for e in store.list_entries(U)]
    assert all_dates == ["2026-01-01", "2026-01-02", "2026-01-03"]

    ranged = [e.date for e in store.list_entries(U, "2026-01-02", "2026-01-03")]
    assert ranged == ["2026-01-02", "2026-01-03"]


def test_delete_entry(store: Storage) -> None:
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    assert store.delete_entry(U, "2026-01-01") is True
    assert store.get_entry(U, "2026-01-01") is None
    # Deleting a non-existent date reports False rather than raising.
    assert store.delete_entry(U, "2026-01-01") is False


def test_settings_defaults_and_partial_update(store: Storage) -> None:
    defaults = store.get_settings(U)
    assert defaults.alpha == 0.10
    assert defaults.window_days == 28
    assert defaults.energy_density == 7700.0
    assert defaults.units == "kg"

    updated = store.update_settings(U, window_days=14, alpha=0.2)
    assert updated.window_days == 14
    assert updated.alpha == 0.2
    # Untouched fields keep their previous values.
    assert updated.energy_density == 7700.0

    # A None value is ignored (partial update), unknown keys are dropped.
    again = store.update_settings(U, alpha=None, bogus="x")  # type: ignore[arg-type]
    assert again.alpha == 0.2


def test_settings_persist_across_reads(store: Storage) -> None:
    store.update_settings(U, target_weight=75.0, target_date="2026-08-31")
    s = store.get_settings(U)
    assert s.target_weight == 75.0
    assert s.target_date == "2026-08-31"


def test_bulk_upsert_many_rows_in_one_transaction(store: Storage) -> None:
    """Regression for the import path: N rows must be written as ONE transaction
    (the per-row commit loop raised "database is locked" on bulk import)."""
    rows = [(f"2026-01-{i:02d}", 80.0 - i * 0.1, 2000 + i) for i in range(1, 16)]
    assert store.upsert_entries(U, rows) == 15
    listed = store.list_entries(U)
    assert len(listed) == 15
    assert listed[0].weight_kg == 79.9
    assert listed[-1].kcal == 2015


def test_bulk_upsert_keeps_upsert_semantics(store: Storage) -> None:
    first = store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    store.upsert_entries(U, [("2026-01-01", 79.5, 1900), ("2026-01-02", 79.4, None)])
    # Same overwrite-by-date behaviour as the single-row path: no duplicate row,
    # created_at preserved, values replaced.
    listed = store.list_entries(U)
    assert len(listed) == 2
    assert listed[0].weight_kg == 79.5
    assert listed[0].created_at == first.created_at


def test_bulk_upsert_is_atomic_on_failure(store: Storage) -> None:
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    # A mid-batch infrastructure failure (unbindable parameter) must roll the whole
    # batch back — no half-imported file.
    bad = [("2026-01-02", 79.9, 1900), ("2026-01-03", {"boom": 1}, 1800)]
    with pytest.raises(sqlite3.Error):
        store.upsert_entries(U, bad)  # type: ignore[arg-type]
    assert [e.date for e in store.list_entries(U)] == ["2026-01-01"]
    # And the connection is left usable (no dangling open transaction).
    store.upsert_entry(U, "2026-01-04", 79.7, 1850)
    assert len(store.list_entries(U)) == 2


def test_concurrent_reads_are_safe(store: Storage) -> None:
    """Regression: FastAPI serves sync handlers from a threadpool, so reads hit the
    shared connection concurrently (e.g. the dashboard fires /estimate and /settings
    in parallel). Unlocked reads corrupted each other's fetches (wrong-shape/None
    rows); all reads must therefore hold the storage lock."""
    for i in range(1, 29):
        store.upsert_entry(U, f"2026-01-{i:02d}", 80.0 - i * 0.05, 2000)

    errors: list[Exception] = []

    def hammer(fn) -> None:
        try:
            for _ in range(2000):
                fn()
        except Exception as exc:  # noqa: BLE001 - any corruption error fails the test
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(lambda: store.get_settings(U),)),
        threading.Thread(target=hammer, args=(lambda: store.list_entries(U),)),
        threading.Thread(target=hammer, args=(lambda: store.get_entry(U, "2026-01-05"),)),
        threading.Thread(target=hammer, args=(lambda: store.get_settings(U),)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# --- multi-user isolation (Phase 1) --------------------------------------- #
# Only one real user exists today, but Phase 2's whole safety story rests on the
# storage layer never leaking one user's data to another. Prove that now, while
# it is cheap, against the adversarial two-user case.
def test_new_user_gets_own_default_settings(store: Storage) -> None:
    other = store.create_user()
    assert other != U
    # A fresh user starts from the same documented defaults, independently.
    assert store.get_settings(other).window_days == 28
    store.update_settings(other, window_days=14)
    assert store.get_settings(other).window_days == 14
    # The default user is untouched by another user's settings change.
    assert store.get_settings(U).window_days == 28


def test_entries_are_isolated_per_user(store: Storage) -> None:
    other = store.create_user()
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    store.upsert_entry(other, "2026-01-01", 60.0, 1500)

    # Same date, two users, two independent rows with independent values.
    assert store.get_entry(U, "2026-01-01").weight_kg == 80.0
    assert store.get_entry(other, "2026-01-01").weight_kg == 60.0
    assert [e.date for e in store.list_entries(U)] == ["2026-01-01"]
    assert len(store.list_entries(other)) == 1

    # A user only ever sees their own entries.
    store.upsert_entry(U, "2026-01-02", 79.8, None)
    assert len(store.list_entries(U)) == 2
    assert len(store.list_entries(other)) == 1


def test_upsert_does_not_overwrite_another_users_row(store: Storage) -> None:
    other = store.create_user()
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    store.upsert_entry(other, "2026-01-01", 60.0, 1500)
    # Rewriting other's day leaves the default user's identical-date row intact.
    store.upsert_entry(other, "2026-01-01", 59.5, 1400)
    assert store.get_entry(U, "2026-01-01").weight_kg == 80.0
    assert store.get_entry(other, "2026-01-01").weight_kg == 59.5


def test_delete_does_not_cross_users(store: Storage) -> None:
    other = store.create_user()
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    store.upsert_entry(other, "2026-01-01", 60.0, 1500)
    # Deleting the default user's day must not touch the other user's same-date row.
    assert store.delete_entry(U, "2026-01-01") is True
    assert store.get_entry(U, "2026-01-01") is None
    assert store.get_entry(other, "2026-01-01").weight_kg == 60.0
    # And the reverse: deleting a date only this user has is a no-op for the other.
    assert store.delete_entry(U, "2026-01-01") is False


def test_delete_all_entries_is_user_scoped(store: Storage) -> None:
    """delete_all_entries removes every entry for the target user only; a second
    user's entries and the first user's settings/target are left intact."""
    other = store.create_user()
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    store.upsert_entry(U, "2026-01-02", 79.8, 2100)
    store.upsert_entry(other, "2026-01-01", 60.0, 1500)
    # Give the first user a target so we can prove settings survive the wipe.
    store.update_settings(U, target_weight=75.0, target_date="2026-06-01")

    deleted = store.delete_all_entries(U)

    assert deleted == 2
    assert store.list_entries(U) == []
    # The other user's row is untouched.
    assert [e.date for e in store.list_entries(other)] == ["2026-01-01"]
    assert store.get_entry(other, "2026-01-01").weight_kg == 60.0
    # Settings/target for the wiped user survive.
    settings = store.get_settings(U)
    assert settings.target_weight == 75.0
    assert settings.target_date == "2026-06-01"


def test_delete_all_entries_on_empty_is_zero(store: Storage) -> None:
    """Deleting when there is nothing to delete is a clean no-op returning 0."""
    assert store.delete_all_entries(U) == 0


def test_orphan_user_id_is_rejected(store: Storage) -> None:
    """Foreign keys are enforced: an entry referencing a non-existent user is
    rejected at the DB level (the Phase-2 invariant that every row has a real
    owner). ``9999`` was never created."""
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_entry(9999, "2026-01-01", 80.0, 2000)
    # The failed write left nothing behind and the connection is still usable.
    assert store.list_entries(9999) == []
    store.upsert_entry(U, "2026-01-01", 80.0, 2000)
    assert len(store.list_entries(U)) == 1
