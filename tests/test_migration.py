"""Migration test: legacy single-user DB -> user-scoped schema (Phase 1).

The live DB on Fly holds real data, so the in-place migration in
:meth:`api.storage.Storage._init_schema` must lose nothing and attribute every
existing row to the default user. These tests build a pre-migration DB with raw
``sqlite3`` (the *old* schema — ``entries`` keyed by ``date``, single ``settings``
row), open :class:`Storage` on it (which triggers the migration), and assert the
result is complete, correctly attributed, structurally correct, and idempotent.
"""

from __future__ import annotations

import sqlite3

import pytest

from api.storage import DEFAULT_USER_ID, Storage

# The old (pre-Phase-1) schema, verbatim, so the migration is exercised against
# exactly what the live DB looks like today.
_OLD_SCHEMA = """
CREATE TABLE entries (
    date       TEXT PRIMARY KEY,
    weight_kg  REAL,
    kcal       INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE settings (
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

# A few entries incl. a weight-only and a calorie-only day, and a *customised*
# settings row (units='lb', window_days=14, a target) — so the test proves values
# are carried across, not silently reset to defaults.
_OLD_ENTRIES = [
    ("2026-01-01", 80.0, 2000, "2026-01-01T08:00:00+00:00", "2026-01-01T08:00:00+00:00"),
    ("2026-01-02", 79.8, None, "2026-01-02T08:00:00+00:00", "2026-01-02T08:00:00+00:00"),
    ("2026-01-03", None, 1850, "2026-01-03T08:00:00+00:00", "2026-01-03T08:00:00+00:00"),
]
_OLD_SETTINGS = (0.15, 14, 7700.0, 250.0, 75.0, "2026-08-31", "lb")


def _make_legacy_db(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.executemany(
            "INSERT INTO entries (date, weight_kg, kcal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            _OLD_ENTRIES,
        )
        conn.execute(
            "INSERT INTO settings (id, alpha, window_days, energy_density, "
            "expected_deficit_kcal, target_weight, target_date, units) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            _OLD_SETTINGS,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def legacy_db_path(tmp_path) -> str:
    path = str(tmp_path / "legacy.db")
    _make_legacy_db(path)
    return path


def test_migration_preserves_all_entries_for_default_user(legacy_db_path: str) -> None:
    store = Storage(legacy_db_path)
    try:
        entries = store.list_entries(DEFAULT_USER_ID)
        # Every legacy row survives, values intact, attributed to the default user.
        assert [e.date for e in entries] == ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert entries[0].weight_kg == 80.0 and entries[0].kcal == 2000
        assert entries[1].weight_kg == 79.8 and entries[1].kcal is None
        assert entries[2].weight_kg is None and entries[2].kcal == 1850
        # Timestamps are carried across unchanged (not reset to migration time).
        assert entries[0].created_at == "2026-01-01T08:00:00+00:00"
    finally:
        store.close()


def test_migration_preserves_settings_values(legacy_db_path: str) -> None:
    store = Storage(legacy_db_path)
    try:
        s = store.get_settings(DEFAULT_USER_ID)
        # The customised legacy settings are carried over verbatim — NOT the defaults.
        assert s.alpha == 0.15
        assert s.window_days == 14
        assert s.target_weight == 75.0
        assert s.target_date == "2026-08-31"
        assert s.units == "lb"
    finally:
        store.close()


def test_migration_produces_composite_primary_key(legacy_db_path: str) -> None:
    store = Storage(legacy_db_path)
    try:
        conn = store._conn  # noqa: SLF001 - inspecting schema shape is the point
        cols = conn.execute("PRAGMA table_info(entries)").fetchall()
        # Both user_id and date are part of the PK (pk > 0), date second.
        pk = {r["name"]: r["pk"] for r in cols if r["pk"] > 0}
        assert pk == {"user_id": 1, "date": 2}
        # users table exists and holds exactly the default user.
        user_ids = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
        assert user_ids == [DEFAULT_USER_ID]
    finally:
        store.close()


def test_migration_is_idempotent(legacy_db_path: str) -> None:
    # First open migrates; second open must be a no-op (already user-scoped).
    first = Storage(legacy_db_path)
    first_dates = [e.date for e in first.list_entries(DEFAULT_USER_ID)]
    first.close()

    second = Storage(legacy_db_path)
    try:
        assert [e.date for e in second.list_entries(DEFAULT_USER_ID)] == first_dates
        assert second.get_settings(DEFAULT_USER_ID).units == "lb"
        # Still exactly one user; migration didn't duplicate anything.
        count = second._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]  # noqa: SLF001
        assert count == 1
    finally:
        second.close()


def test_migrated_db_enforces_foreign_keys(legacy_db_path: str) -> None:
    """After migration the FK is live: an orphan entry is rejected on the real
    (file-backed) DB, not just in-memory."""
    store = Storage(legacy_db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert_entry(9999, "2026-02-01", 70.0, 1800)
    finally:
        store.close()


# --- Phase-2 auth migration ----------------------------------------------- #
# A legacy DB migrates through BOTH steps at once (user-scoping then auth); a
# Phase-1 DB (already user-scoped, no auth columns) exercises just the auth step.
def test_legacy_db_gains_auth_schema(legacy_db_path: str) -> None:
    store = Storage(legacy_db_path)
    try:
        conn = store._conn  # noqa: SLF001
        user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        assert {"email", "password_hash"} <= user_cols
        # sessions table exists and the pre-bootstrap user stays uncredentialed.
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
            ).fetchone()
            is not None
        )
        user = store.get_user(DEFAULT_USER_ID)
        assert user.email is None and user.password_hash is None
    finally:
        store.close()


def _make_phase1_db(path: str) -> None:
    """A Phase-1-shaped DB: user-scoped (composite PK) but no auth columns."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL);
            CREATE TABLE entries (
                user_id INTEGER NOT NULL REFERENCES users(id), date TEXT NOT NULL,
                weight_kg REAL, kcal INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, date));
            CREATE TABLE settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(id), alpha REAL NOT NULL,
                window_days INTEGER NOT NULL, energy_density REAL NOT NULL,
                expected_deficit_kcal REAL NOT NULL, target_weight REAL, target_date TEXT,
                units TEXT NOT NULL);
            INSERT INTO users VALUES (1, '2026-01-01T00:00:00+00:00');
            INSERT INTO settings VALUES (1, 0.1, 21, 7700.0, 300.0, NULL, NULL, 'kg');
            INSERT INTO entries VALUES (1, '2026-01-01', 80.0, 2000, 't', 't');
            INSERT INTO entries VALUES (1, '2026-01-02', 79.8, NULL, 't', 't');
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_phase1_db_gains_auth_schema_without_data_loss(tmp_path) -> None:
    path = str(tmp_path / "phase1.db")
    _make_phase1_db(path)

    store = Storage(path)
    try:
        # Auth columns + sessions added; existing entries/settings untouched.
        assert len(store.list_entries(DEFAULT_USER_ID)) == 2
        assert store.get_settings(DEFAULT_USER_ID).window_days == 21
        assert store.find_uncredentialed_user_id() == DEFAULT_USER_ID
        # And the new auth machinery works on the upgraded DB.
        store.set_user_credentials(DEFAULT_USER_ID, "u@test.local", "hash")
        assert store.get_user_by_email("u@test.local").id == DEFAULT_USER_ID
    finally:
        store.close()

    # Idempotent: re-opening the already-upgraded DB is a no-op.
    again = Storage(path)
    try:
        assert len(again.list_entries(DEFAULT_USER_ID)) == 2
        assert again.get_user_by_email("u@test.local") is not None
    finally:
        again.close()


# --- Phase-4 demographics migration --------------------------------------- #
def test_legacy_db_gains_demographics_columns(legacy_db_path: str) -> None:
    # The full migration chain (legacy → user-scoped → auth → demographics) leaves
    # the settings table with the Phase-4 columns, all NULL, data preserved.
    store = Storage(legacy_db_path)
    try:
        cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(settings)")}  # noqa: SLF001
        assert {"age", "sex", "height_cm", "activity_level"} <= cols
        s = store.get_settings(DEFAULT_USER_ID)
        assert s.age is None and s.sex is None and s.height_cm is None and s.activity_level is None
        # Existing settings values are untouched by the column adds.
        assert s.units == "lb"
    finally:
        store.close()


def test_phase3_db_gains_demographics_without_data_loss(tmp_path) -> None:
    """A Phase-3-shaped DB (user-scoped + auth, no demographic columns) gains them
    on open, non-destructively, and the provisional inputs then round-trip."""
    path = str(tmp_path / "phase3.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT,
            created_at TEXT NOT NULL);
        CREATE UNIQUE INDEX idx_users_email ON users(email);
        CREATE TABLE entries (user_id INTEGER NOT NULL REFERENCES users(id), date TEXT NOT NULL,
            weight_kg REAL, kcal INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, date));
        CREATE TABLE settings (user_id INTEGER PRIMARY KEY REFERENCES users(id),
            alpha REAL NOT NULL, window_days INTEGER NOT NULL, energy_density REAL NOT NULL,
            expected_deficit_kcal REAL NOT NULL, target_weight REAL, target_date TEXT,
            units TEXT NOT NULL);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        INSERT INTO users VALUES (1, NULL, NULL, 't');
        INSERT INTO settings VALUES (1, 0.1, 28, 7700.0, 300.0, 75.0, '2026-08-31', 'kg');
        INSERT INTO entries VALUES (1, '2026-01-01', 80.0, 2000, 't', 't');
        """
    )
    conn.commit()
    conn.close()

    store = Storage(path)
    try:
        cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(settings)")}  # noqa: SLF001
        assert {"age", "sex", "height_cm", "activity_level"} <= cols
        # Existing settings + entry preserved; demographics start empty then persist.
        assert store.get_settings(DEFAULT_USER_ID).target_weight == 75.0
        assert len(store.list_entries(DEFAULT_USER_ID)) == 1
        store.update_settings(DEFAULT_USER_ID, age=30, sex="male", height_cm=180.0)
        assert store.get_settings(DEFAULT_USER_ID).age == 30
    finally:
        store.close()


# --- password-reset-tokens migration -------------------------------------- #
def test_legacy_db_gains_reset_tokens_table(legacy_db_path: str) -> None:
    # The full migration chain also creates the password_reset_tokens table.
    store = Storage(legacy_db_path)
    try:
        assert (
            store._conn.execute(  # noqa: SLF001
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'"
            ).fetchone()
            is not None
        )
        cols = {
            r["name"]
            for r in store._conn.execute("PRAGMA table_info(password_reset_tokens)")  # noqa: SLF001
        }
        assert {"token_hash", "user_id", "created_at", "expires_at", "used_at"} <= cols
    finally:
        store.close()


def test_pre_reset_db_gains_tokens_table_without_data_loss(tmp_path) -> None:
    """A DB with everything EXCEPT the reset-tokens table (a pre-this-phase shape)
    gains it on open, non-destructively and idempotently, and reset tokens then work."""
    path = str(tmp_path / "pre_reset.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT,
            created_at TEXT NOT NULL);
        CREATE UNIQUE INDEX idx_users_email ON users(email);
        CREATE TABLE entries (user_id INTEGER NOT NULL REFERENCES users(id), date TEXT NOT NULL,
            weight_kg REAL, kcal INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, date));
        CREATE TABLE settings (user_id INTEGER PRIMARY KEY REFERENCES users(id),
            alpha REAL NOT NULL, window_days INTEGER NOT NULL, energy_density REAL NOT NULL,
            expected_deficit_kcal REAL NOT NULL, target_weight REAL, target_date TEXT,
            units TEXT NOT NULL, age INTEGER, sex TEXT, height_cm REAL, activity_level TEXT);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        INSERT INTO users VALUES (1, 'a@b.com', 'hash', 't');
        INSERT INTO settings VALUES
            (1, 0.1, 28, 7700.0, 300.0, NULL, NULL, 'kg', NULL, NULL, NULL, NULL);
        INSERT INTO entries VALUES (1, '2026-01-01', 80.0, 2000, 't', 't');
        """
    )
    conn.commit()
    conn.close()

    store = Storage(path)
    try:
        assert (
            store._conn.execute(  # noqa: SLF001
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'"
            ).fetchone()
            is not None
        )
        # Existing data untouched.
        assert store.get_user_by_email("a@b.com").id == DEFAULT_USER_ID
        assert len(store.list_entries(DEFAULT_USER_ID)) == 1
        # And the new machinery works on the upgraded DB.
        store.create_reset_token("hash123", DEFAULT_USER_ID, "2099-01-01T00:00:00+00:00")
        assert store.consume_reset_token("hash123", "2026-07-12T00:00:00+00:00") == DEFAULT_USER_ID
    finally:
        store.close()

    # Idempotent: re-opening the upgraded DB is a no-op.
    again = Storage(path)
    try:
        assert len(again.list_entries(DEFAULT_USER_ID)) == 1
    finally:
        again.close()
