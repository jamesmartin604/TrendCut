"""Authentication tests (Phase 2).

Drive the real HTTP auth surface through Starlette's ``TestClient``: login sets a
working session, bad credentials are rejected with a single generic message, the
data routes require a session, sessions expire and can be revoked, and a stored
password is a hash (never plaintext) that still verifies. Also proves end-to-end
that one authenticated user cannot see or touch another's data.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from api import auth
from api.app import create_app
from api.bootstrap_user import bootstrap
from api.storage import Storage

_EMAIL = "alice@test.local"
_PASSWORD = "correct horse battery staple"


@pytest.fixture
def storage() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()


@pytest.fixture
def client(storage: Storage) -> TestClient:
    """Unauthenticated client with the default user credentialed (not logged in)."""
    bootstrap(storage, _EMAIL, _PASSWORD)
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        yield c


def _login(c: TestClient, email: str = _EMAIL, password: str = _PASSWORD):
    return c.post("/auth/login", json={"email": email, "password": password})


# --- login / session ------------------------------------------------------ #
def test_login_success_sets_working_session(client: TestClient) -> None:
    r = _login(client)
    assert r.status_code == 200
    # Cookie set, and a protected route now works.
    assert auth.SESSION_COOKIE in r.cookies or auth.SESSION_COOKIE in client.cookies
    assert client.get("/settings").status_code == 200
    me = client.get("/auth/me").json()
    assert me == {"authenticated": True, "email": _EMAIL}


def test_wrong_password_rejected(client: TestClient) -> None:
    r = _login(client, password="wrong")
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"
    # No session was established.
    assert client.get("/settings").status_code == 401


def test_unknown_email_gives_same_generic_failure(client: TestClient) -> None:
    known_bad = _login(client, password="wrong")
    unknown = _login(client, email="nobody@test.local", password="whatever")
    # Identical status and message — no account-existence oracle.
    assert unknown.status_code == known_bad.status_code == 401
    assert unknown.json()["detail"] == known_bad.json()["detail"] == "Invalid email or password"


def test_data_routes_require_session(client: TestClient) -> None:
    # Every data route 401s without a session.
    assert client.get("/settings").status_code == 401
    assert client.get("/estimate").status_code == 401
    assert client.get("/entries").status_code == 401
    assert client.get("/tdee-history").status_code == 401
    assert client.put("/entries/2026-01-01", json={"weight_kg": 80.0}).status_code == 401
    assert client.get("/entries/export").status_code == 401


def test_me_is_public_and_reports_anonymous(client: TestClient) -> None:
    # /auth/me never 401s — it's the frontend's is-logged-in probe.
    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json() == {"authenticated": False, "email": None}


def test_logout_invalidates_session(client: TestClient) -> None:
    _login(client)
    assert client.get("/settings").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    # Session is gone server-side; the cookie is cleared.
    assert client.get("/settings").status_code == 401
    assert client.get("/auth/me").json()["authenticated"] is False


def test_expired_session_is_rejected(storage: Storage) -> None:
    # Insert a session that is already expired and confirm the seam rejects it.
    bootstrap(storage, _EMAIL, _PASSWORD)
    app = create_app(storage, cookie_secure=False)
    user = storage.get_user_by_email(_EMAIL)
    token = auth.new_session_token()
    past = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=1)).isoformat()
    storage.create_session(auth.hash_token(token), user.id, past)
    with TestClient(app) as c:
        c.cookies.set(auth.SESSION_COOKIE, token)
        assert c.get("/settings").status_code == 401


def test_stored_password_is_hashed_but_verifies(storage: Storage) -> None:
    bootstrap(storage, _EMAIL, _PASSWORD)
    user = storage.get_user_by_email(_EMAIL)
    # Never stored as plaintext…
    assert user.password_hash is not None
    assert _PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")
    # …but the stored hash verifies the real password and rejects a wrong one.
    assert auth.verify_password(user.password_hash, _PASSWORD) is True
    assert auth.verify_password(user.password_hash, "nope") is False


def test_session_token_is_stored_hashed_not_raw(storage: Storage) -> None:
    bootstrap(storage, _EMAIL, _PASSWORD)
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        _login(c)
        raw = c.cookies.get(auth.SESSION_COOKIE)
    # The raw cookie token is NOT what's in the DB; the SHA-256 hash is.
    rows = storage._conn.execute("SELECT token_hash FROM sessions").fetchall()  # noqa: SLF001
    stored = [r["token_hash"] for r in rows]
    assert raw not in stored
    assert auth.hash_token(raw) in stored


# --- isolation through the auth layer ------------------------------------- #
def test_authenticated_users_are_isolated(storage: Storage) -> None:
    """User A's session cannot read or mutate user B's entries or settings — the
    Phase-1 storage isolation, now proven end-to-end through real logins."""
    # A claims the default user; B is a second credentialed account.
    bootstrap(storage, "a@test.local", "password-a")
    storage.create_credentialed_user("b@test.local", auth.hash_password("password-b"))
    app = create_app(storage, cookie_secure=False)

    with TestClient(app) as ca, TestClient(app) as cb:
        ca.post("/auth/login", json={"email": "a@test.local", "password": "password-a"})
        cb.post("/auth/login", json={"email": "b@test.local", "password": "password-b"})

        ca.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": 2000})
        cb.put("/entries/2026-01-01", json={"weight_kg": 60.0, "kcal": 1500})

        # Each sees only their own value for the same date.
        assert ca.get("/entries/2026-01-01").json()["weight_kg"] == 80.0
        assert cb.get("/entries/2026-01-01").json()["weight_kg"] == 60.0
        assert len(ca.get("/entries").json()) == 1
        assert len(cb.get("/entries").json()) == 1

        # A settings change by B does not touch A.
        cb.put("/settings", json={"window_days": 14})
        assert ca.get("/settings").json()["window_days"] == 28
        assert cb.get("/settings").json()["window_days"] == 14

        # B deleting their date leaves A's identical-date entry intact.
        assert cb.delete("/entries/2026-01-01").status_code == 204
        assert ca.get("/entries/2026-01-01").json()["weight_kg"] == 80.0


# --- registration (Phase 2.5: open signup, no email verification) --------- #
def _register(c: TestClient, email: str, password: str):
    return c.post("/auth/register", json={"email": email, "password": password})


def test_register_creates_user_settings_and_session(storage: Storage) -> None:
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        r = _register(c, "new@test.local", "password123")
        assert r.status_code == 200
        # Immediately authenticated (auto-login) …
        assert c.get("/auth/me").json() == {"authenticated": True, "email": "new@test.local"}
        # … with a working session on the data routes …
        assert c.get("/settings").status_code == 200
        # … a default settings row (the documented default window) …
        assert c.get("/settings").json()["window_days"] == 28
        # … and a zero-data account (isolated from any other user).
        assert c.get("/entries").json() == []

    # The account persists and the password verifies as a stored hash (not plaintext).
    user = storage.get_user_by_email("new@test.local")
    assert user is not None
    assert user.password_hash and "password123" not in user.password_hash
    assert auth.verify_password(user.password_hash, "password123")


def test_register_duplicate_email_is_clean_and_creates_no_second_account(
    storage: Storage,
) -> None:
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        assert _register(c, "dup@test.local", "password123").status_code == 200
        before = storage._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]  # noqa: SLF001
        # Same email (different casing/space) → clean 409, not a 500/traceback.
        r = _register(c, "  DUP@test.local ", "different-pass")
        assert r.status_code == 409
        assert r.json()["detail"] == "That email is already registered."
        after = storage._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]  # noqa: SLF001
        assert after == before  # no partial/second account


def test_register_rejects_invalid_email_and_short_password(storage: Storage) -> None:
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        assert _register(c, "not-an-email", "password123").status_code == 400
        assert _register(c, "a@b.com", "short").status_code == 400
        # An over-long password is rejected too (argon2 abuse guard).
        assert _register(c, "a@b.com", "x" * (auth.MAX_PASSWORD_LENGTH + 1)).status_code == 400
        # None of the rejected attempts created an account.
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM users WHERE email IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


def test_register_normalizes_email_casing_and_whitespace(storage: Storage) -> None:
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        assert _register(c, "  MixedCase@Test.Local  ", "password123").status_code == 200
        c.post("/auth/logout")
        # Stored normalized, and login works regardless of the casing the user types.
        assert storage.get_user_by_email("mixedcase@test.local") is not None
        assert _login(c, "MIXEDCASE@TEST.LOCAL", "password123").status_code == 200


def test_existing_lowercase_account_still_logs_in_after_normalization(
    storage: Storage,
) -> None:
    """Regression: login started normalizing emails this phase. An account created
    the old way (already lowercase, e.g. via the Phase-2 bootstrap) must still log
    in unchanged."""
    bootstrap(storage, "legacy@test.local", "password123")
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        assert _login(c, "legacy@test.local", "password123").status_code == 200


def test_new_registrant_is_isolated_from_existing_user(storage: Storage) -> None:
    # An existing account with data (the claimed default user) …
    bootstrap(storage, "existing@test.local", "password-existing")
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as existing, TestClient(app) as fresh:
        existing.post(
            "/auth/login",
            json={"email": "existing@test.local", "password": "password-existing"},
        )
        existing.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": 2000})
        existing.put("/settings", json={"window_days": 14})

        # … and a brand-new self-registered account.
        _register(fresh, "fresh@test.local", "password-fresh")

        # The newcomer sees only their own empty data and default settings.
        assert fresh.get("/entries").json() == []
        assert fresh.get("/settings").json()["window_days"] == 28
        # They cannot read or overwrite the existing user's day.
        assert fresh.get("/entries/2026-01-01").status_code == 404
        fresh.put("/entries/2026-01-01", json={"weight_kg": 55.0, "kcal": 1200})
        assert existing.get("/entries/2026-01-01").json()["weight_kg"] == 80.0
        # And the existing user's settings are untouched by the newcomer.
        assert existing.get("/settings").json()["window_days"] == 14
