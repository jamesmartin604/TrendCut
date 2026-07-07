"""Attach login credentials to an account — the first-account bootstrap.

There is no public self-service signup yet (that is a later phase), so this CLI is
how an account gets a password:

    python -m api.bootstrap_user --email you@example.com          # prompts for password
    python -m api.bootstrap_user --email you@example.com --password 's3cret'

Behaviour (claim vs create):

* If an **uncredentialed** user exists (the ``DEFAULT_USER_ID`` account carried over
  from Phase 1, before anyone had a password), its credentials are **set in place**
  — all of that user's entries and settings are preserved.
* Otherwise a **new** credentialed user is created.
* If the email is already taken by a credentialed account, the command **refuses**
  rather than silently overwriting it.

Run it against the same database the app uses (``TRENDCUT_DB``, default
``trendcut.db``). On the live Fly deploy, snapshot the volume first and make sure
nothing else holds the DB open (a stray ``--reload`` process, DB Browser).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from api.auth import hash_password
from api.storage import Storage


class BootstrapError(Exception):
    """A clean, expected refusal (duplicate email, empty input) — not a crash."""


def bootstrap(store: Storage, email: str, password: str) -> str:
    """Claim the uncredentialed user or create a new one. Returns a status message.

    Raises :class:`BootstrapError` on a duplicate email or empty input, so callers
    can print a clean message and exit non-zero instead of dumping a traceback.
    """
    email = email.strip()
    if not email:
        raise BootstrapError("email must not be empty")
    if not password:
        raise BootstrapError("password must not be empty")

    existing = store.get_user_by_email(email)
    if existing is not None:
        # An email already in use by a *credentialed* account is a hard refusal; we
        # never clobber someone's login. (An uncredentialed row can't have an email,
        # so a hit here always means credentials already exist.)
        raise BootstrapError(
            f"email {email!r} is already registered (user id {existing.id}); refusing to "
            "overwrite. Choose a different email or reset via the database directly."
        )

    password_hash = hash_password(password)
    claim_id = store.find_uncredentialed_user_id()
    if claim_id is not None:
        store.set_user_credentials(claim_id, email, password_hash)
        return f"Claimed existing user id {claim_id} as {email} (data preserved)."
    new_id = store.create_credentialed_user(email, password_hash)
    return f"Created new user id {new_id} as {email}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m api.bootstrap_user",
        description="Attach login credentials to an account (claim the existing user "
        "or create a new one).",
    )
    parser.add_argument("--email", required=True, help="login email")
    parser.add_argument(
        "--password",
        help="login password (omit to be prompted, so it stays out of shell history)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TRENDCUT_DB", "trendcut.db"),
        help="SQLite path (default: $TRENDCUT_DB or trendcut.db)",
    )
    args = parser.parse_args(argv)

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords did not match.", file=sys.stderr)
            return 2

    store = Storage(args.db)
    try:
        message = bootstrap(store, args.email, password)
    except BootstrapError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
