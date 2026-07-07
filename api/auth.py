"""Authentication primitives: password hashing and opaque session tokens.

Kept separate from :mod:`api.storage` (which stays framework-free and stores only
opaque strings) and from :mod:`api.app` (routing). Nothing here knows about HTTP.

Design notes:

* Passwords are hashed with **argon2id** (argon2-cffi defaults). ``verify_password``
  is constant-time via the library. Plaintext is never stored, logged, or returned.
* Login runs a verification even when the email is unknown, against a module-level
  **dummy hash produced by the same** :class:`~argon2.PasswordHasher` **instance**,
  so the argon2 parameters are identical by construction and an attacker cannot
  distinguish "no such user" from "wrong password" by timing.
* Session tokens are opaque (``secrets.token_urlsafe``). Only a **SHA-256 hash** of
  the token is stored server-side, so a leak of the DB does not hand out live
  sessions (the raw token lives only in the user's cookie). Lookups hash the
  presented token and match on the hash.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

__all__ = [
    "SESSION_COOKIE",
    "SESSION_TTL_DAYS",
    "hash_password",
    "hash_token",
    "new_session_token",
    "session_expiry",
    "verify_password",
]

# Cookie carrying the raw session token. Server stores only hash_token(value).
SESSION_COOKIE = "trendcut_session"

# Fixed (non-sliding) session lifetime. A session is valid for this long from
# creation; re-login is required afterwards. Chosen fixed for simplicity this phase.
SESSION_TTL_DAYS = 30

_ph = PasswordHasher()

# A valid argon2id hash of a throwaway secret, produced by the SAME hasher, so an
# unknown-email login can run a real verify with identical parameters/timing. The
# input is random and discarded — nothing ever verifies against it successfully.
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Return an argon2id hash string (algorithm + params + salt are self-contained)."""
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-time password check.

    When ``password_hash`` is ``None`` (an uncredentialed or unknown user), still
    perform a verify against the dummy hash so the timing matches the real path,
    then return ``False``.
    """
    target = password_hash if password_hash is not None else _DUMMY_HASH
    try:
        _ph.verify(target, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Any malformed-hash / backend error is an auth failure, never a 500.
        return False
    # A None hash means there was no real user to authenticate, regardless of match.
    return password_hash is not None


def new_session_token() -> str:
    """A fresh opaque session token (URL-safe, ~256 bits). Returned to the client;
    only its :func:`hash_token` is persisted."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 of a session token — the value stored/looked up server-side.

    SHA-256 (not argon2) is deliberate: the token is already high-entropy random,
    so there is nothing to brute-force, and lookups must be a single fast hash."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def session_expiry(created: datetime | None = None) -> str:
    """ISO expiry timestamp ``SESSION_TTL_DAYS`` after ``created`` (default now)."""
    base = created if created is not None else _now()
    return (base + timedelta(days=SESSION_TTL_DAYS)).isoformat()
