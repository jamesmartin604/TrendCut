"""Bootstrap CLI tests (Phase 2).

The bootstrap command is how the first account gets a password. It must:
* claim the pre-existing uncredentialed user (Phase-1 user 1) so their entries and
  settings are preserved — never orphan the real data under a fresh user;
* create a new user only when there is no uncredentialed one to claim;
* refuse (cleanly, no traceback) to overwrite an already-credentialed account.
"""

from __future__ import annotations

import pytest

from api import auth
from api.bootstrap_user import BootstrapError, bootstrap
from api.storage import DEFAULT_USER_ID, Storage


@pytest.fixture
def store() -> Storage:
    s = Storage.migrated(":memory:")
    yield s
    s.close()


def test_claims_default_user_preserving_data(store: Storage) -> None:
    # Seed the uncredentialed default user with real data.
    store.upsert_entry(DEFAULT_USER_ID, "2026-01-01", 80.0, 2000)
    store.upsert_entry(DEFAULT_USER_ID, "2026-01-02", 79.8, None)
    store.update_settings(DEFAULT_USER_ID, window_days=14, units="lb")

    msg = bootstrap(store, "me@test.local", "s3cret-pass")
    assert "Claimed" in msg and str(DEFAULT_USER_ID) in msg

    user = store.get_user_by_email("me@test.local")
    # Same user id — data was claimed in place, not moved to a new account.
    assert user.id == DEFAULT_USER_ID
    assert auth.verify_password(user.password_hash, "s3cret-pass")
    assert len(store.list_entries(DEFAULT_USER_ID)) == 2
    s = store.get_settings(DEFAULT_USER_ID)
    assert s.window_days == 14 and s.units == "lb"
    # No uncredentialed user remains.
    assert store.find_uncredentialed_user_id() is None


def test_creates_new_user_when_none_to_claim(store: Storage) -> None:
    # Claim the only uncredentialed user first…
    bootstrap(store, "first@test.local", "password-one")
    # …so a second bootstrap has nothing to claim and must create a new account.
    msg = bootstrap(store, "second@test.local", "password-two")
    assert "Created new user" in msg

    first = store.get_user_by_email("first@test.local")
    second = store.get_user_by_email("second@test.local")
    assert second.id != first.id
    # The new user gets its own default settings.
    assert store.get_settings(second.id).window_days == 28


def test_refuses_duplicate_email_without_overwriting(store: Storage) -> None:
    bootstrap(store, "taken@test.local", "original-pass")
    original = store.get_user_by_email("taken@test.local")

    with pytest.raises(BootstrapError, match="already registered"):
        bootstrap(store, "taken@test.local", "different-pass")

    # The existing credential is untouched — still verifies the original password.
    after = store.get_user_by_email("taken@test.local")
    assert after.password_hash == original.password_hash
    assert auth.verify_password(after.password_hash, "original-pass")


def test_rejects_empty_email_or_password(store: Storage) -> None:
    with pytest.raises(BootstrapError, match="email"):
        bootstrap(store, "   ", "password")
    with pytest.raises(BootstrapError, match="password"):
        bootstrap(store, "someone@test.local", "")
