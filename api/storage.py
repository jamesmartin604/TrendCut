"""SQLite persistence for raw entries and settings.

Deliberately framework-free: this module knows nothing about HTTP and can be
tested directly (see ``tests/test_storage.py``). The route handlers delegate all
persistence here.

Schema (PRD §9.1):

* ``entries`` — one row per calendar day, ``date`` (ISO ``YYYY-MM-DD``) is the
  primary key. ``weight_kg`` and ``kcal`` are independently nullable (you may log
  a weigh-in without food, or vice versa). Re-logging a date **upserts** (updates
  in place), never duplicates. ``created_at`` is preserved across upserts;
  ``updated_at`` moves.
* ``settings`` — a single row (``id = 1``) holding the estimator/plan knobs.

Nothing derived (trend/TDEE/plateau) is ever stored; those are recomputed from
``entries`` on demand.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = ["Entry", "Settings", "Storage"]


@dataclass(frozen=True)
class Entry:
    date: str  # ISO YYYY-MM-DD
    weight_kg: float | None
    kcal: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Settings:
    alpha: float
    window_days: int
    energy_density: float
    expected_deficit_kcal: float
    target_weight: float | None
    target_date: str | None
    units: str


# Defaults for the single settings row. window_days = 28 because a 14-day window
# (the PRD's nominal default) is too short to recover TDEE within ~100 kcal; 28 is
# the smallest window that does (see estimator recovery tests). All are editable.
_DEFAULT_SETTINGS = Settings(
    alpha=0.10,
    window_days=28,
    energy_density=7700.0,
    expected_deficit_kcal=300.0,
    target_weight=None,
    target_date=None,
    units="kg",
)

_SETTINGS_FIELDS = (
    "alpha",
    "window_days",
    "energy_density",
    "expected_deficit_kcal",
    "target_weight",
    "target_date",
    "units",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Storage:
    """A thin SQLite wrapper. Construct with ``":memory:"`` for tests."""

    def __init__(self, db_path: str = ":memory:") -> None:
        # check_same_thread=False: FastAPI runs sync handlers in a threadpool. A
        # single connection guarded by a lock is plenty for a single-user app.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    date       TEXT PRIMARY KEY,
                    weight_kg  REAL,
                    kcal       INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    id                    INTEGER PRIMARY KEY CHECK (id = 1),
                    alpha                 REAL NOT NULL,
                    window_days           INTEGER NOT NULL,
                    energy_density        REAL NOT NULL,
                    expected_deficit_kcal REAL NOT NULL,
                    target_weight         REAL,
                    target_date           TEXT,
                    units                 TEXT NOT NULL
                );
                """
            )
            row = self._conn.execute("SELECT 1 FROM settings WHERE id = 1").fetchone()
            if row is None:
                d = _DEFAULT_SETTINGS
                self._conn.execute(
                    "INSERT INTO settings (id, alpha, window_days, energy_density, "
                    "expected_deficit_kcal, target_weight, target_date, units) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        d.alpha,
                        d.window_days,
                        d.energy_density,
                        d.expected_deficit_kcal,
                        d.target_weight,
                        d.target_date,
                        d.units,
                    ),
                )
            self._conn.commit()

    # --- entries ----------------------------------------------------------- #
    def upsert_entry(self, date: str, weight_kg: float | None, kcal: int | None) -> Entry:
        """Insert or overwrite the entry for ``date`` (created_at preserved)."""
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO entries (date, weight_kg, kcal, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    weight_kg  = excluded.weight_kg,
                    kcal       = excluded.kcal,
                    updated_at = excluded.updated_at
                """,
                (date, weight_kg, kcal, now, now),
            )
            self._conn.commit()
        return self.get_entry(date)  # type: ignore[return-value]

    def get_entry(self, date: str) -> Entry | None:
        # Reads take the lock too: the connection is shared across the threadpool
        # FastAPI runs sync handlers in, and unsynchronized concurrent fetches on
        # one sqlite3 connection corrupt each other's results.
        with self._lock:
            row = self._conn.execute("SELECT * FROM entries WHERE date = ?", (date,)).fetchone()
        return _row_to_entry(row) if row is not None else None

    def list_entries(self, start: str | None = None, end: str | None = None) -> list[Entry]:
        """All entries (ordered by date), optionally restricted to [start, end]."""
        with self._lock:
            if start is not None and end is not None:
                rows = self._conn.execute(
                    "SELECT * FROM entries WHERE date BETWEEN ? AND ? ORDER BY date",
                    (start, end),
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM entries ORDER BY date").fetchall()
        return [_row_to_entry(r) for r in rows]

    def delete_entry(self, date: str) -> bool:
        """Delete the entry; return True if a row was removed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM entries WHERE date = ?", (date,))
            self._conn.commit()
        return cur.rowcount > 0

    # --- settings ---------------------------------------------------------- #
    def get_settings(self) -> Settings:
        with self._lock:
            row = self._conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return Settings(
            alpha=row["alpha"],
            window_days=row["window_days"],
            energy_density=row["energy_density"],
            expected_deficit_kcal=row["expected_deficit_kcal"],
            target_weight=row["target_weight"],
            target_date=row["target_date"],
            units=row["units"],
        )

    def update_settings(self, **fields: object) -> Settings:
        """Partial update; only recognised keys with non-None values are applied."""
        updates = {k: v for k, v in fields.items() if k in _SETTINGS_FIELDS and v is not None}
        if updates:
            assignments = ", ".join(f"{k} = ?" for k in updates)
            with self._lock:
                self._conn.execute(
                    f"UPDATE settings SET {assignments} WHERE id = 1",
                    tuple(updates.values()),
                )
                self._conn.commit()
        return self.get_settings()

    def close(self) -> None:
        self._conn.close()


def _row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        date=row["date"],
        weight_kg=row["weight_kg"],
        kcal=row["kcal"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
