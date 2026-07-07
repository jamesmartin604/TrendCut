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
