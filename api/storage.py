"""SQLite persistence for raw entries and settings.

Deliberately framework-free: this module knows nothing about HTTP and can be
tested directly (see ``tests/test_storage.py``). The route handlers delegate all
persistence here.

Schema (multi-user-ready — Phase 1):

* ``users`` — one row per user; minimal for now (``id`` PK + ``created_at``).
  Identity/email/auth columns are Phase 2. A single default user (``id =
  DEFAULT_USER_ID``) is seeded so the current single-user surface is unchanged.
* ``entries`` — one row per (user, calendar day). Composite primary key
  ``(user_id, date)`` (ISO ``YYYY-MM-DD``). ``weight_kg`` and ``kcal`` are
  independently nullable (you may log a weigh-in without food, or vice versa).
  Re-logging a date **upserts** (updates in place), never duplicates.
  ``created_at`` is preserved across upserts; ``updated_at`` moves.
* ``settings`` — one row per user (``user_id`` PK), holding the estimator/plan
  knobs plus optional demographics (``age``/``sex``/``height_cm``/
  ``activity_level``) for the Phase-4 provisional estimate.
* ``users`` also carries ``email`` (UNIQUE via index; NULL until claimed) and
  ``password_hash`` for authentication (Phase 2).
* ``sessions`` — opaque login sessions: ``token_hash`` (SHA-256 of the cookie
  token, never the raw token) PK, ``user_id``, ``created_at``, ``expires_at``.
* ``password_reset_tokens`` — single-use password-reset tokens, stored hashed
  (``token_hash`` SHA-256) with a short ``expires_at`` and a ``used_at`` marker.

Every storage method takes an explicit ``user_id`` — there is no hidden default
inside this layer; the caller (the API's ``get_current_user_id`` seam) supplies
it. This keeps the module honestly multi-tenant while the HTTP surface stays
single-user for now.

Nothing derived (trend/TDEE/plateau) is ever stored; those are recomputed from
``entries`` on demand.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "DEFAULT_USER_ID",
    "Entry",
    "SchemaOutOfDateError",
    "Settings",
    "Storage",
    "User",
]

# The implicit user for the (still single-user) HTTP surface. Phase 1 seeds and
# backfills everything to this id; Phase 2's auth replaces the API seam that
# resolves it, not this constant's role as "the original account".
DEFAULT_USER_ID = 1


class SchemaOutOfDateError(RuntimeError):
    """Raised at boot when the database schema is missing tables/columns the code
    requires. The app refuses to start (rather than silently migrating) so the
    operator can snapshot the volume and run ``python -m api.migrate`` deliberately."""


# The schema the current code REQUIRES to run. Boot verifies against this and
# refuses to start on any mismatch; migrate() is what brings a DB up to it. This is
# a *required-columns-subset* contract: every listed table/column MUST be present,
# but extra columns (or tables) never fail verification — so a volume that is ahead
# of, or incidentally augmented beyond, this baseline still boots. Keep in lockstep
# with _create_fresh_schema and the _ensure_* migration steps.
_EXPECTED_SCHEMA: dict[str, set[str]] = {
    "users": {"id", "email", "password_hash", "created_at"},
    "entries": {"user_id", "date", "weight_kg", "kcal", "created_at", "updated_at"},
    "settings": {
        "user_id",
        "alpha",
        "window_days",
        "energy_density",
        "expected_deficit_kcal",
        "target_weight",
        "target_date",
        "units",
        "age",
        "sex",
        "height_cm",
        "activity_level",
    },
    "sessions": {"token_hash", "user_id", "created_at", "expires_at"},
    "password_reset_tokens": {
        "token_hash",
        "user_id",
        "created_at",
        "expires_at",
        "used_at",
    },
}


@dataclass(frozen=True)
class Entry:
    date: str  # ISO YYYY-MM-DD
    weight_kg: float | None
    kcal: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class User:
    id: int
    email: str | None  # NULL until credentials are attached (bootstrap claims user 1)
    password_hash: str | None  # argon2id string; NULL for an uncredentialed user
    created_at: str


@dataclass(frozen=True)
class Settings:
    alpha: float
    window_days: int
    energy_density: float
    expected_deficit_kcal: float
    target_weight: float | None
    target_date: str | None
    units: str
    # Optional demographics for the Phase-4 provisional (day-1 formula) estimate.
    # All nullable; the provisional is only computed when the full set is present.
    age: int | None = None
    sex: str | None = None  # "male" | "female"
    height_cm: float | None = None
    activity_level: str | None = None  # sedentary|light|moderate|active|very_active


# Defaults for a user's settings row. window_days = 28 because a 14-day window
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
    age=None,
    sex=None,
    height_cm=None,
    activity_level=None,
)

_SETTINGS_FIELDS = (
    "alpha",
    "window_days",
    "energy_density",
    "expected_deficit_kcal",
    "target_weight",
    "target_date",
    "units",
    "age",
    "sex",
    "height_cm",
    "activity_level",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Storage:
    """A thin SQLite wrapper. Construct with ``":memory:"`` for tests."""

    def __init__(self, db_path: str = ":memory:") -> None:
        # check_same_thread=False: FastAPI runs sync handlers in a threadpool. A
        # single connection guarded by a lock is plenty for a single-user app.
        # timeout=30: if some *other* connection to the same file (a second app
        # process, a DB browser) briefly holds the write lock, wait for it instead
        # of raising "database is locked" immediately.
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        # Enforce foreign keys (off by default in sqlite3) so an entry/settings row
        # can never reference a non-existent user. MUST run outside any transaction
        # — SQLite silently ignores this pragma mid-transaction — so it is set here,
        # before migrate() opens the migration's BEGIN IMMEDIATE.
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        # DELIBERATELY no schema work here: construction is side-effect-free so that
        # merely opening the DB (importing the app, an admin CLI, boot verification)
        # never mutates the volume. Schema creation/migration is the explicit
        # migrate() step (python -m api.migrate); boot only verify_schema()s.

    @classmethod
    def migrated(cls, db_path: str = ":memory:") -> Storage:
        """Construct a Storage and bring its schema up to date, then return it.

        A convenience for the migrate CLI, admin CLIs, and tests: an *explicit*,
        clearly-named opt-in to migration (unlike the old constructor side effect).
        The app's boot path never calls this — it verify_schema()s instead."""
        store = cls(db_path)
        store.migrate()
        return store

    # --- schema / migration ------------------------------------------------ #
    def migrate(self) -> None:
        """Create the schema, or migrate an old single-user DB in place.

        The explicit migration step (``python -m api.migrate`` invokes this). Was
        formerly run from ``__init__`` on every open; it is now a deliberate call so
        boot can verify-and-refuse rather than silently mutate the volume.

        Three cases, all idempotent (safe to run repeatedly):

        * fresh DB — create the user-scoped tables and seed the default user;
        * already migrated — ensure the default user + its settings exist, done;
        * legacy DB (``entries`` has no ``user_id``) — rebuild to the composite-PK
          schema, backfilling every existing row to ``DEFAULT_USER_ID``.

        Then, independently, :meth:`_ensure_auth_schema` adds the Phase-2 auth
        columns and ``sessions`` table if they are missing (a Phase-1 DB already has
        user scoping but not auth).
        """
        with self._lock:
            if not self._table_exists("entries"):
                self._create_fresh_schema()
            elif "user_id" not in self._columns("entries"):
                self._migrate_to_user_scoped()
            else:
                self._ensure_default_user()
            # Flush the base-schema work (and close any implicit transaction) before
            # the auth migration opens its own BEGIN IMMEDIATE.
            self._conn.commit()
            self._ensure_auth_schema()
            self._ensure_demographics_schema()
            self._ensure_reset_tokens_schema()

    def verify_schema(self) -> list[str]:
        """Read-only check that the DB has everything the code requires.

        Returns a list of human-readable problems (empty ⇒ the schema is current).
        Touches no data and writes nothing — safe to call on the live volume at boot.
        A *required-columns-subset* check: extra columns/tables are fine, so a volume
        that is ahead of this baseline still passes (protects the live data)."""
        problems: list[str] = []
        for table, required in _EXPECTED_SCHEMA.items():
            if not self._table_exists(table):
                problems.append(f"missing table '{table}'")
                continue
            missing = required - self._columns(table)
            if missing:
                cols = ", ".join(sorted(missing))
                problems.append(f"table '{table}' is missing column(s): {cols}")
        return problems

    def verify_or_raise(self) -> None:
        """Raise :class:`SchemaOutOfDateError` if the schema is out of date.

        Boot calls this instead of migrating: it names each mismatch and tells the
        operator exactly how to fix it, rather than silently mutating the volume."""
        problems = self.verify_schema()
        if problems:
            bullets = "\n".join(f"  - {p}" for p in problems)
            raise SchemaOutOfDateError(
                f"Database schema at {self._db_path!r} is out of date; refusing to "
                f"start (boot does not migrate).\n{bullets}\n\n"
                f"Snapshot the volume, then run:  python -m api.migrate"
                f"  (against TRENDCUT_DB={self._db_path!r})"
            )

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _create_fresh_schema(self) -> None:
        """Brand-new DB: create user-scoped tables and seed the default user."""
        self._conn.executescript(
            """
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY,
                email         TEXT,
                password_hash TEXT,
                created_at    TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_users_email ON users(email);
            CREATE TABLE entries (
                user_id    INTEGER NOT NULL REFERENCES users(id),
                date       TEXT NOT NULL,
                weight_kg  REAL,
                kcal       INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, date)
            );
            CREATE TABLE settings (
                user_id               INTEGER PRIMARY KEY REFERENCES users(id),
                alpha                 REAL NOT NULL,
                window_days           INTEGER NOT NULL,
                energy_density        REAL NOT NULL,
                expected_deficit_kcal REAL NOT NULL,
                target_weight         REAL,
                target_date           TEXT,
                units                 TEXT NOT NULL,
                age                   INTEGER,
                sex                   TEXT,
                height_cm             REAL,
                activity_level        TEXT
            );
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE password_reset_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at    TEXT
            );
            """
        )
        self._ensure_default_user()

    def _ensure_default_user(self) -> None:
        """Seed ``DEFAULT_USER_ID`` and its default settings if missing (idempotent)."""
        exists = self._conn.execute(
            "SELECT 1 FROM users WHERE id = ?", (DEFAULT_USER_ID,)
        ).fetchone()
        if exists is None:
            self._conn.execute(
                "INSERT INTO users (id, created_at) VALUES (?, ?)", (DEFAULT_USER_ID, _now())
            )
        self._insert_default_settings_if_missing(DEFAULT_USER_ID)

    def _insert_default_settings_if_missing(self, user_id: int) -> None:
        exists = self._conn.execute(
            "SELECT 1 FROM settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        if exists is None:
            d = _DEFAULT_SETTINGS
            self._conn.execute(
                "INSERT INTO settings (user_id, alpha, window_days, energy_density, "
                "expected_deficit_kcal, target_weight, target_date, units) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    d.alpha,
                    d.window_days,
                    d.energy_density,
                    d.expected_deficit_kcal,
                    d.target_weight,
                    d.target_date,
                    d.units,
                ),
            )

    def _migrate_to_user_scoped(self) -> None:
        """Rebuild a legacy single-user DB into the composite-PK schema.

        SQLite cannot ALTER a primary key, so each table is rebuilt (create-new,
        copy, drop-old, rename) — the standard "table rebuild" recipe. Everything
        runs in one explicit ``BEGIN IMMEDIATE`` transaction: transactional DDL
        means a failure anywhere rolls the whole thing back and the original DB is
        untouched (this project has been bitten by half-applied writes before). We
        grab the write lock up front (IMMEDIATE) so we fail fast rather than
        mid-rebuild if another connection holds it. All existing rows are
        attributed to ``DEFAULT_USER_ID``; a row-count check guards against silent
        data loss before we drop the originals.
        """
        conn = self._conn
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        try:
            entries_before = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            had_settings = (
                conn.execute("SELECT COUNT(*) FROM settings WHERE id = 1").fetchone()[0] > 0
            )

            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
            conn.execute("INSERT INTO users (id, created_at) VALUES (?, ?)", (DEFAULT_USER_ID, now))

            # entries: rebuild with the composite PK, backfilling user_id.
            conn.execute(
                """
                CREATE TABLE entries_new (
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    date       TEXT NOT NULL,
                    weight_kg  REAL,
                    kcal       INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, date)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO entries_new (user_id, date, weight_kg, kcal, created_at, updated_at)
                SELECT ?, date, weight_kg, kcal, created_at, updated_at FROM entries
                """,
                (DEFAULT_USER_ID,),
            )
            entries_after = conn.execute("SELECT COUNT(*) FROM entries_new").fetchone()[0]
            if entries_after != entries_before:
                raise RuntimeError(
                    f"migration lost rows: entries {entries_before} -> {entries_after}"
                )
            conn.execute("DROP TABLE entries")
            conn.execute("ALTER TABLE entries_new RENAME TO entries")

            # settings: single id=1 row becomes one row per user, keyed by user_id.
            conn.execute(
                """
                CREATE TABLE settings_new (
                    user_id               INTEGER PRIMARY KEY REFERENCES users(id),
                    alpha                 REAL NOT NULL,
                    window_days           INTEGER NOT NULL,
                    energy_density        REAL NOT NULL,
                    expected_deficit_kcal REAL NOT NULL,
                    target_weight         REAL,
                    target_date           TEXT,
                    units                 TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO settings_new (user_id, alpha, window_days, energy_density,
                    expected_deficit_kcal, target_weight, target_date, units)
                SELECT ?, alpha, window_days, energy_density,
                    expected_deficit_kcal, target_weight, target_date, units
                FROM settings WHERE id = 1
                """,
                (DEFAULT_USER_ID,),
            )
            conn.execute("DROP TABLE settings")
            conn.execute("ALTER TABLE settings_new RENAME TO settings")
            # A legacy DB should always have had its settings row, but if somehow
            # not, seed defaults so the default user is never left without settings.
            if not had_settings:
                self._insert_default_settings_if_missing(DEFAULT_USER_ID)

            # Belt-and-braces: no dangling references survived the rebuild.
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"migration left FK violations: {violations!r}")

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_auth_schema(self) -> None:
        """Add the Phase-2 auth columns and ``sessions`` table if missing (idempotent).

        Only nullable columns are added, so a plain ``ALTER TABLE ADD COLUMN`` is
        enough — no table rebuild. ``email`` gets a UNIQUE *index* (not an inline
        UNIQUE constraint, which ``ADD COLUMN`` cannot express); a unique index lets
        multiple NULLs coexist, so uncredentialed users (incl. the pre-bootstrap
        user 1) are fine while any real email stays unique. Wrapped in one
        transaction with rollback, like the Phase-1 migration.
        """
        if "email" in self._columns("users"):
            return
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_demographics_schema(self) -> None:
        """Add the Phase-4 demographic columns to ``settings`` if missing (idempotent).

        Age / sex / height / activity level feed the opt-in provisional (day-1
        formula) TDEE. All nullable, so a plain ``ALTER TABLE ADD COLUMN`` suffices
        (no rebuild); wrapped in one transaction with rollback like the other
        migrations.
        """
        if "age" in self._columns("settings"):
            return
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE settings ADD COLUMN age INTEGER")
            conn.execute("ALTER TABLE settings ADD COLUMN sex TEXT")
            conn.execute("ALTER TABLE settings ADD COLUMN height_cm REAL")
            conn.execute("ALTER TABLE settings ADD COLUMN activity_level TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_reset_tokens_schema(self) -> None:
        """Add the ``password_reset_tokens`` table if missing (idempotent).

        Stores only the SHA-256 hash of each reset token, single-use (``used_at``),
        with a short expiry — a leak of this table hands out no usable links. A plain
        ``CREATE TABLE IF NOT EXISTS`` in one transaction, like the other migrations.
        """
        if self._table_exists("password_reset_tokens"):
            return
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                CREATE TABLE password_reset_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at    TEXT
                )
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # --- users ------------------------------------------------------------- #
    def create_user(self) -> int:
        """Create a new user (with default settings) and return its id.

        Phase 1 has no auth, so this is used by tests to prove per-user isolation;
        Phase 2's signup will call it behind the auth seam. The default user keeps
        ``id = DEFAULT_USER_ID``; additional users autoincrement above it.
        """
        with self._lock:
            cur = self._conn.execute("INSERT INTO users (created_at) VALUES (?)", (_now(),))
            user_id = int(cur.lastrowid)
            self._insert_default_settings_if_missing(user_id)
            self._conn.commit()
        return user_id

    def get_user(self, user_id: int) -> User | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row is not None else None

    def get_user_by_email(self, email: str) -> User | None:
        """The user with this email, or None. Email uniqueness is index-enforced."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return _row_to_user(row) if row is not None else None

    def find_uncredentialed_user_id(self) -> int | None:
        """The lowest-id user without credentials (e.g. the pre-bootstrap user 1),
        or None. This is the account the bootstrap CLI claims so existing data is
        preserved rather than orphaned under a brand-new user."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM users WHERE email IS NULL ORDER BY id LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def set_user_credentials(self, user_id: int, email: str, password_hash: str) -> None:
        """Attach credentials to an existing user (claim). Raises
        :class:`sqlite3.IntegrityError` if the email is already taken."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
                (email, password_hash, user_id),
            )
            self._conn.commit()

    def create_credentialed_user(self, email: str, password_hash: str) -> int:
        """Create a new user with credentials (+ default settings); return its id.

        The user row and its settings row are written **atomically** — both commit
        together or neither does, so a failure (including a duplicate-email
        ``IntegrityError`` from the UNIQUE index, which fires on the users insert)
        can never leave a half-created account behind. On any error the batch is
        rolled back and the exception propagates (the duplicate case is the caller's
        registration guard)."""
        now = _now()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (email, password_hash, now),
                )
                user_id = int(cur.lastrowid)
                self._insert_default_settings_if_missing(user_id)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return user_id

    # --- sessions ---------------------------------------------------------- #
    def create_session(self, token_hash: str, user_id: int, expires_at: str) -> None:
        """Persist a session. ``token_hash`` is the SHA-256 of the cookie token —
        the raw token is never stored, so a DB leak yields no usable sessions."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, user_id, _now(), expires_at),
            )
            self._conn.commit()

    def get_session_user_id(self, token_hash: str, now: str) -> int | None:
        """The user for a live (non-expired) session, or None. ``now`` is an ISO
        timestamp; a session with ``expires_at <= now`` is treated as absent."""
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
        return int(row["user_id"]) if row is not None else None

    def delete_session(self, token_hash: str) -> bool:
        """Remove a session (logout). Returns True if one existed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_expired_sessions(self, now: str) -> int:
        """Purge sessions with ``expires_at <= now``; return how many were removed.
        Called opportunistically on login/logout to keep the table from growing."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            self._conn.commit()
        return cur.rowcount

    def delete_user_sessions(self, user_id: int) -> int:
        """Remove ALL of a user's sessions; return how many. Used on password reset so
        the reset locks out anyone still holding an old session/password."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._conn.commit()
        return cur.rowcount

    # --- password reset ---------------------------------------------------- #
    def update_user_password(self, user_id: int, password_hash: str) -> None:
        """Set a user's password hash (used by the reset flow)."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
            )
            self._conn.commit()

    def create_reset_token(self, token_hash: str, user_id: int, expires_at: str) -> None:
        """Persist a password-reset token (only its SHA-256 hash is stored)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO password_reset_tokens (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, user_id, _now(), expires_at),
            )
            self._conn.commit()

    def consume_reset_token(self, token_hash: str, now: str) -> int | None:
        """Atomically claim a valid reset token, returning its ``user_id`` or None.

        Single-use is enforced in ONE statement — ``UPDATE ... SET used_at WHERE
        token_hash = ? AND used_at IS NULL AND expires_at > ?`` — and we act on the
        row count, so there is no read-then-write race: a second concurrent reset with
        the same token updates zero rows and gets None. Returns None for an unknown,
        expired, or already-used token.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE password_reset_tokens SET used_at = ? "
                "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                (now, token_hash, now),
            )
            if cur.rowcount == 0:
                self._conn.commit()
                return None
            # We exclusively claimed the row above, so this read is safe.
            row = self._conn.execute(
                "SELECT user_id FROM password_reset_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            self._conn.commit()
        return int(row["user_id"]) if row is not None else None

    def delete_expired_reset_tokens(self, now: str) -> int:
        """Purge expired reset tokens; return how many. Called opportunistically."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at <= ?", (now,)
            )
            self._conn.commit()
        return cur.rowcount

    # --- entries ----------------------------------------------------------- #
    def upsert_entry(
        self, user_id: int, date: str, weight_kg: float | None, kcal: int | None
    ) -> Entry:
        """Insert or overwrite this user's entry for ``date`` (created_at preserved)."""
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO entries (user_id, date, weight_kg, kcal, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    weight_kg  = excluded.weight_kg,
                    kcal       = excluded.kcal,
                    updated_at = excluded.updated_at
                """,
                (user_id, date, weight_kg, kcal, now, now),
            )
            self._conn.commit()
        return self.get_entry(user_id, date)  # type: ignore[return-value]

    def upsert_entries(self, user_id: int, rows: list[tuple[str, float | None, int | None]]) -> int:
        """Upsert many ``(date, weight_kg, kcal)`` rows for one user in ONE transaction.

        The bulk-import path. One BEGIN…COMMIT for the whole batch instead of a
        transaction per row: the per-row commits gave any other connection on the
        file N chances to grab the write lock mid-loop ("database is locked" on
        import), and a single transaction is also simply faster. The batch is
        **atomic** — on any failure it rolls back and the DB is unchanged (rows the
        parser skipped never reach this method, so good rows in a file with bad
        rows still import; only an infrastructure failure mid-write rolls back).
        Per-row semantics are identical to :meth:`upsert_entry`: upsert by
        ``(user_id, date)``, ``created_at`` preserved, ``updated_at`` moved.
        """
        now = _now()
        params = [(user_id, date, weight_kg, kcal, now, now) for date, weight_kg, kcal in rows]
        with self._lock:
            try:
                self._conn.executemany(
                    """
                    INSERT INTO entries (user_id, date, weight_kg, kcal, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, date) DO UPDATE SET
                        weight_kg  = excluded.weight_kg,
                        kcal       = excluded.kcal,
                        updated_at = excluded.updated_at
                    """,
                    params,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(params)

    def get_entry(self, user_id: int, date: str) -> Entry | None:
        # Reads take the lock too: the connection is shared across the threadpool
        # FastAPI runs sync handlers in, and unsynchronized concurrent fetches on
        # one sqlite3 connection corrupt each other's results.
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM entries WHERE user_id = ? AND date = ?", (user_id, date)
            ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def list_entries(
        self, user_id: int, start: str | None = None, end: str | None = None
    ) -> list[Entry]:
        """This user's entries (ordered by date), optionally restricted to [start, end]."""
        with self._lock:
            if start is not None and end is not None:
                rows = self._conn.execute(
                    "SELECT * FROM entries WHERE user_id = ? AND date BETWEEN ? AND ? "
                    "ORDER BY date",
                    (user_id, start, end),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM entries WHERE user_id = ? ORDER BY date", (user_id,)
                ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def delete_entry(self, user_id: int, date: str) -> bool:
        """Delete this user's entry; return True if a row was removed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM entries WHERE user_id = ? AND date = ?", (user_id, date)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def delete_all_entries(self, user_id: int) -> int:
        """Delete ALL of this user's entries in one transaction; return the count.

        User-scoped by construction (``WHERE user_id = ?``) — never a global wipe.
        Only ``entries`` rows go: the account, settings, target, and demographics are
        untouched. Atomic like :meth:`upsert_entries` — one BEGIN…COMMIT, rolled back
        on any failure so a partial delete can never be left committed."""
        with self._lock:
            try:
                cur = self._conn.execute("DELETE FROM entries WHERE user_id = ?", (user_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return cur.rowcount

    # --- settings ---------------------------------------------------------- #
    def get_settings(self, user_id: int) -> Settings:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM settings WHERE user_id = ?", (user_id,)
            ).fetchone()
        return Settings(
            alpha=row["alpha"],
            window_days=row["window_days"],
            energy_density=row["energy_density"],
            expected_deficit_kcal=row["expected_deficit_kcal"],
            target_weight=row["target_weight"],
            target_date=row["target_date"],
            units=row["units"],
            age=row["age"],
            sex=row["sex"],
            height_cm=row["height_cm"],
            activity_level=row["activity_level"],
        )

    def update_settings(self, user_id: int, **fields: object) -> Settings:
        """Partial update; only recognised keys with non-None values are applied."""
        updates = {k: v for k, v in fields.items() if k in _SETTINGS_FIELDS and v is not None}
        if updates:
            assignments = ", ".join(f"{k} = ?" for k in updates)
            with self._lock:
                self._conn.execute(
                    f"UPDATE settings SET {assignments} WHERE user_id = ?",
                    (*updates.values(), user_id),
                )
                self._conn.commit()
        return self.get_settings(user_id)

    def close(self) -> None:
        self._conn.close()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
    )


def _row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        date=row["date"],
        weight_kg=row["weight_kg"],
        kcal=row["kcal"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
