"""Schema verification + explicit-migration tests (boot no longer auto-migrates).

Construction is side-effect-free now: opening a DB never writes schema. ``migrate()``
(via ``python -m api.migrate``) is the only thing that creates/upgrades schema; boot
calls ``verify_schema``/``verify_or_raise`` and refuses to start on a mismatch rather
than silently rewriting the volume. These tests pin all four corners of that contract.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import migrate as migrate_cli
from api.app import create_app
from api.storage import SchemaOutOfDateError, Storage


def test_construction_does_not_create_schema(tmp_path) -> None:
    """Merely opening a fresh DB must NOT write any schema (no boot-time side effect)."""
    path = str(tmp_path / "fresh.db")
    store = Storage(path)
    try:
        assert store.verify_schema()  # non-empty: nothing has been created
        # Specifically, none of the expected tables exist yet.
        tables = {
            r[0]
            for r in store._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == set()
    finally:
        store.close()


def test_verify_schema_clean_after_migrate(tmp_path) -> None:
    """A migrated DB satisfies the required-columns contract (no problems)."""
    store = Storage.migrated(str(tmp_path / "migrated.db"))
    try:
        assert store.verify_schema() == []
    finally:
        store.close()


def test_verify_or_raise_fails_loudly_on_fresh_db(tmp_path) -> None:
    """A fresh/empty DB fails boot loudly — names the mismatch and the fix command,
    rather than silently migrating or crashing ambiguously."""
    path = str(tmp_path / "empty.db")
    store = Storage(path)
    try:
        with pytest.raises(SchemaOutOfDateError) as exc:
            store.verify_or_raise()
        msg = str(exc.value)
        # Names a concrete missing piece and the operator instruction.
        assert "missing table 'users'" in msg
        assert "python -m api.migrate" in msg
    finally:
        store.close()


def test_verify_or_raise_fails_on_missing_table(tmp_path) -> None:
    """A DB that is migrated EXCEPT for one table fails verification, naming it."""
    path = str(tmp_path / "partial.db")
    store = Storage.migrated(path)
    store._conn.execute("DROP TABLE password_reset_tokens")  # noqa: SLF001
    store._conn.commit()
    try:
        problems = store.verify_schema()
        assert problems == ["missing table 'password_reset_tokens'"]
        with pytest.raises(SchemaOutOfDateError):
            store.verify_or_raise()
    finally:
        store.close()


def test_verify_or_raise_fails_on_missing_column(tmp_path) -> None:
    """A DB missing a required column (a stale pre-demographics settings table) fails
    verification with the column named — not a subset false-pass."""
    path = str(tmp_path / "stale.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT,
            created_at TEXT NOT NULL);
        CREATE TABLE entries (user_id INTEGER NOT NULL, date TEXT NOT NULL,
            weight_kg REAL, kcal INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, date));
        CREATE TABLE settings (user_id INTEGER PRIMARY KEY, alpha REAL NOT NULL,
            window_days INTEGER NOT NULL, energy_density REAL NOT NULL,
            expected_deficit_kcal REAL NOT NULL, target_weight REAL, target_date TEXT,
            units TEXT NOT NULL);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE password_reset_tokens (token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            used_at TEXT);
        """
    )
    conn.commit()
    conn.close()
    store = Storage(path)
    try:
        problems = store.verify_schema()
        assert len(problems) == 1
        assert "settings" in problems[0]
        # The demographic columns are the ones missing.
        for col in ("age", "sex", "height_cm", "activity_level"):
            assert col in problems[0]
    finally:
        store.close()


def test_extra_columns_do_not_fail_verification(tmp_path) -> None:
    """Required-columns-subset: a volume with EXTRA columns still passes (protects a
    live DB that is incidentally ahead of the code's baseline)."""
    store = Storage.migrated(str(tmp_path / "extra.db"))
    store._conn.execute("ALTER TABLE settings ADD COLUMN future_flag INTEGER")  # noqa: SLF001
    store._conn.commit()
    try:
        assert store.verify_schema() == []
    finally:
        store.close()


def test_migrate_cli_brings_fresh_db_up_to_schema(tmp_path, capsys) -> None:
    """`python -m api.migrate --db <fresh>` exits 0 and produces a clean schema."""
    path = str(tmp_path / "cli.db")
    rc = migrate_cli.main(["--db", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "up to date" in out

    # The DB is now current (verified with a fresh read-only open).
    store = Storage(path)
    try:
        assert store.verify_schema() == []
    finally:
        store.close()


def test_migrate_cli_is_idempotent(tmp_path, capsys) -> None:
    """Re-running migrate on an already-current DB is a no-op that still exits 0."""
    path = str(tmp_path / "twice.db")
    assert migrate_cli.main(["--db", path]) == 0
    capsys.readouterr()
    assert migrate_cli.main(["--db", path]) == 0
    assert "already current" in capsys.readouterr().out


def test_create_app_refuses_unmigrated_storage() -> None:
    """Boot verifies the storage it is handed: an un-migrated DB makes create_app
    raise loudly instead of starting (and instead of migrating it)."""
    with pytest.raises(SchemaOutOfDateError):
        create_app(Storage(":memory:"), cookie_secure=False)


def test_create_app_starts_on_migrated_storage() -> None:
    """The happy path: a migrated storage boots and serves."""
    app = create_app(Storage.migrated(":memory:"), cookie_secure=False)
    with TestClient(app) as c:
        assert c.get("/health").json() == {"ok": True}
