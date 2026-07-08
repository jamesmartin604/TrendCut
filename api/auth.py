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
    "MAX_EMAIL_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "SESSION_COOKIE",
    "SESSION_TTL_DAYS",
    "RegistrationError",
    "hash_password",
    "hash_token",
    "new_session_token",
    "normalize_email",
    "session_expiry",
    "validate_registration",
    "verify_password",
]

# Cookie carrying the raw session token. Server stores only hash_token(value).
SESSION_COOKIE = "trendcut_session"

# Fixed (non-sliding) session lifetime. A session is valid for this long from
# creation; re-login is required afterwards. Chosen fixed for simplicity this phase.
SESSION_TTL_DAYS = 30

# Registration input limits. Emails are unverified login handles, so validation is
# deliberately light. The password *upper* bound matters for safety: argon2 hashes
# the whole input, so an unbounded password is a cheap way to force expensive
# hashing — cap it. 254 is the RFC 5321 max email length.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
MAX_EMAIL_LENGTH = 254

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


def normalize_email(email: str) -> str:
    """Canonical form for storage and lookup: trimmed and lowercased.

    Applied consistently by registration, login, and the bootstrap CLI so that
    ``Foo@X.com`` and ``foo@x.com`` resolve to the same account (casing/whitespace
    can never mint a duplicate-looking account)."""
    return email.strip().lower()


class RegistrationError(ValueError):
    """A human-readable registration-input rejection (maps to a 400)."""


def validate_registration(email: str, password: str) -> str:
    """Validate signup input; return the normalized email or raise RegistrationError.

    Intentionally minimal — emails are unverified, so this only rejects the clearly
    unusable (no ``@``, embedded whitespace, empty local/domain, over-length) rather
    than trying to prove deliverability. This is the authoritative server-side
    check; any client-side validation is UX only.
    """
    normalized = normalize_email(email)
    local, sep, domain = normalized.partition("@")
    if not sep or not local or not domain or any(ch.isspace() for ch in normalized):
        raise RegistrationError("Enter a valid email address.")
    if len(normalized) > MAX_EMAIL_LENGTH:
        raise RegistrationError("That email address is too long.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise RegistrationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise RegistrationError("That password is too long.")
    return normalized
