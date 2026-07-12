"""Generate a password-reset link from the command line — self-host recovery and
operator break-glass, no email provider required.

    python -m api.reset_password --email you@example.com
    python -m api.reset_password --email you@example.com --base-url https://trendcut.example

Resolves the user by (normalized) email, stores a hashed single-use token, and
prints the reset URL to the console for the operator to use or relay. Unlike the
public ``/auth/request-reset`` endpoint, this reports "no such user" plainly: the
operator running a shell command is a trusted context, so it is not an enumeration
oracle.

Run it against the same database the app uses (``TRENDCUT_DB``). On the live Fly
deploy, snapshot the volume first and make sure nothing else holds the DB open (a
stray ``--reload`` process, DB Browser).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

from api.auth import hash_token, new_reset_token, normalize_email, reset_token_expiry
from api.storage import Storage


class ResetError(Exception):
    """A clean, expected refusal (no such user) — not a crash."""


def generate_reset(store: Storage, email: str, base_url: str) -> str:
    """Create a reset token for the user and return the reset URL. Raises
    :class:`ResetError` if no user has that email."""
    email = normalize_email(email)
    user = store.get_user_by_email(email)
    if user is None:
        raise ResetError(f"no user with email {email!r}")
    token = new_reset_token()
    store.create_reset_token(hash_token(token), user.id, reset_token_expiry())
    store.delete_expired_reset_tokens(_dt.datetime.now(_dt.UTC).isoformat())
    return f"{base_url.rstrip('/')}/reset.html?token={token}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m api.reset_password",
        description="Generate a password-reset link for a user (self-host / break-glass).",
    )
    parser.add_argument("--email", required=True, help="the account's email")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TRENDCUT_BASE_URL", "http://localhost:8000"),
        help="base URL for the reset link (default: $TRENDCUT_BASE_URL or http://localhost:8000)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TRENDCUT_DB", "trendcut.db"),
        help="SQLite path (default: $TRENDCUT_DB or trendcut.db)",
    )
    args = parser.parse_args(argv)

    store = Storage(args.db)
    try:
        url = generate_reset(store, args.email, args.base_url)
    except ResetError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print("Password reset link (expires soon, single use):")
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
