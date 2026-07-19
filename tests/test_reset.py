"""Password-reset / account-recovery tests.

Covers the security-critical properties: generic non-enumerating request-reset,
hashed-at-rest single-use expiring tokens, session invalidation on reset, password
validation, the delivery seam (via a fake adapter — no real SMTP), and the CLI path.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from api import auth
from api.app import create_app
from api.storage import Storage

_EMAIL = "reset-me@test.local"
_PASSWORD = "original-password"


class FakeDelivery:
    """Captures the reset link instead of sending it, so tests can read the token."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, email: str, reset_url: str) -> None:
        self.sent.append((email, reset_url))

    @property
    def last_token(self) -> str:
        return self.sent[-1][1].split("token=")[1]


@pytest.fixture
def delivery() -> FakeDelivery:
    return FakeDelivery()


@pytest.fixture
def storage() -> Storage:
    s = Storage.migrated(":memory:")
    yield s
    s.close()


@pytest.fixture
def client(storage: Storage, delivery: FakeDelivery) -> TestClient:
    app = create_app(storage, cookie_secure=False, reset_delivery=delivery)
    with TestClient(app) as c:
        c.post("/auth/register", json={"email": _EMAIL, "password": _PASSWORD})
        yield c


def _request_reset(c: TestClient, email: str):
    return c.post("/auth/request-reset", json={"email": email})


# --- request-reset: no enumeration ---------------------------------------- #
def test_request_reset_is_generic_for_known_and_unknown(client: TestClient) -> None:
    known = _request_reset(client, _EMAIL)
    unknown = _request_reset(client, "nobody@test.local")
    assert known.status_code == unknown.status_code == 200
    # Byte-identical response — no account-existence disclosure.
    assert known.json() == unknown.json()
    assert "reset link has been sent" in known.json()["message"]


def test_request_reset_only_delivers_for_a_real_account(
    client: TestClient, delivery: FakeDelivery
) -> None:
    _request_reset(client, _EMAIL)
    _request_reset(client, "nobody@test.local")
    # A link was generated for the real account only (delivery captured once).
    assert [e for (e, _) in delivery.sent] == [_EMAIL]


def test_request_reset_normalizes_email(client: TestClient, delivery: FakeDelivery) -> None:
    _request_reset(client, "  Reset-Me@TEST.local ")
    assert delivery.sent[-1][0] == _EMAIL  # normalized to the stored form


# --- token storage: hashed at rest ---------------------------------------- #
def test_token_is_stored_hashed_not_raw(
    client: TestClient, storage: Storage, delivery: FakeDelivery
) -> None:
    _request_reset(client, _EMAIL)
    raw = delivery.last_token
    stored = [
        r["token_hash"]
        for r in storage._conn.execute("SELECT token_hash FROM password_reset_tokens")  # noqa: SLF001
    ]
    assert raw not in stored
    assert auth.hash_token(raw) in stored


# --- reset: happy path + lockout ------------------------------------------ #
def test_valid_token_resets_password_and_rotates_credentials(
    client: TestClient, delivery: FakeDelivery
) -> None:
    _request_reset(client, _EMAIL)
    token = delivery.last_token
    assert (
        client.post(
            "/auth/reset", json={"token": token, "new_password": "brand-new-pass"}
        ).status_code
        == 200
    )

    # New password works; old one no longer does.
    assert (
        client.post("/auth/login", json={"email": _EMAIL, "password": "brand-new-pass"}).status_code
        == 200
    )
    fresh = TestClient(client.app)
    assert (
        fresh.post("/auth/login", json={"email": _EMAIL, "password": _PASSWORD}).status_code == 401
    )


def test_reset_invalidates_existing_sessions(storage: Storage, delivery: FakeDelivery) -> None:
    app = create_app(storage, cookie_secure=False, reset_delivery=delivery)
    # A logged-in session (register auto-logs-in).
    holder = TestClient(app)
    holder.post("/auth/register", json={"email": _EMAIL, "password": _PASSWORD})
    assert holder.get("/settings").status_code == 200

    # Reset via a separate client.
    resetter = TestClient(app)
    resetter.post("/auth/request-reset", json={"email": _EMAIL})
    resetter.post(
        "/auth/reset", json={"token": delivery.last_token, "new_password": "brand-new-pass"}
    )

    # The holder's pre-reset session no longer authenticates.
    assert holder.get("/settings").status_code == 401


def test_used_token_is_rejected(client: TestClient, delivery: FakeDelivery) -> None:
    _request_reset(client, _EMAIL)
    token = delivery.last_token
    assert (
        client.post(
            "/auth/reset", json={"token": token, "new_password": "first-new-pass"}
        ).status_code
        == 200
    )
    # Second use of the same token is rejected (single-use).
    r = client.post("/auth/reset", json={"token": token, "new_password": "second-new-pass"})
    assert r.status_code == 400


def test_expired_token_is_rejected(
    client: TestClient, storage: Storage, delivery: FakeDelivery
) -> None:
    _request_reset(client, _EMAIL)
    token = delivery.last_token
    # Force the stored token to be already expired.
    past = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=1)).isoformat()
    storage._conn.execute(  # noqa: SLF001
        "UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?",
        (past, auth.hash_token(token)),
    )
    storage._conn.commit()  # noqa: SLF001
    assert (
        client.post(
            "/auth/reset", json={"token": token, "new_password": "brand-new-pass"}
        ).status_code
        == 400
    )


def test_unknown_token_is_rejected(client: TestClient) -> None:
    r = client.post(
        "/auth/reset", json={"token": "not-a-real-token", "new_password": "brand-new-pass"}
    )
    assert r.status_code == 400


def test_reset_enforces_password_rules_without_burning_the_token(
    client: TestClient, delivery: FakeDelivery
) -> None:
    _request_reset(client, _EMAIL)
    token = delivery.last_token
    # Too short → 400, and the token is NOT consumed (validated before consume).
    assert (
        client.post("/auth/reset", json={"token": token, "new_password": "short"}).status_code
        == 400
    )
    # Too long → 400.
    huge = "x" * (auth.MAX_PASSWORD_LENGTH + 1)
    assert (
        client.post("/auth/reset", json={"token": token, "new_password": huge}).status_code == 400
    )
    # The token still works for a valid password.
    assert (
        client.post(
            "/auth/reset", json={"token": token, "new_password": "valid-new-pass"}
        ).status_code
        == 200
    )


# --- CLI ------------------------------------------------------------------- #
def test_cli_generates_working_token_and_refuses_missing_user(storage: Storage) -> None:
    from api.reset_password import ResetError, generate_reset

    storage.create_credentialed_user(_EMAIL, auth.hash_password(_PASSWORD))
    url = generate_reset(storage, "  Reset-Me@TEST.local ", "https://trendcut.example")
    assert url.startswith("https://trendcut.example/reset.html?token=")
    token = url.split("token=")[1]

    # The CLI-generated token actually works through the API.
    app = create_app(storage, cookie_secure=False, reset_delivery=FakeDelivery())
    with TestClient(app) as c:
        assert (
            c.post("/auth/reset", json={"token": token, "new_password": "cli-new-pass"}).status_code
            == 200
        )

    with pytest.raises(ResetError):
        generate_reset(storage, "nobody@test.local", "https://trendcut.example")
