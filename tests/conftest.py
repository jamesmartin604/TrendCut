"""Test session setup that runs BEFORE any test module is imported.

Two things happen here, in this order, at collection time:

1. ``TRENDCUT_DB`` is pointed at a throwaway temp file (not the real ``trendcut.db``).
   Importing :mod:`api.app` executes its module-level ``app = create_app()``, which
   opens ``$TRENDCUT_DB`` — without this redirect, merely importing the app for a test
   would touch the developer's live database. (Historically that live open *migrated*
   it; now it *verifies* it, which would also fail the whole suite if the local DB were
   stale or missing.)

2. That temp DB is migrated to the current schema, so the module-level boot
   verification (``verify_or_raise``) passes and the app imports cleanly.

conftest.py is imported by pytest before the test modules, so setting the env var and
migrating here is guaranteed to precede any ``from api.app import ...``.
"""

from __future__ import annotations

import os
import tempfile

# Isolate the app's default DB to a temp file for the whole session, before api.app
# (and thus its module-level create_app()) is imported by any test.
_tmp_dir = tempfile.mkdtemp(prefix="trendcut-test-")
os.environ["TRENDCUT_DB"] = os.path.join(_tmp_dir, "trendcut.db")

# Bring that DB up to schema so boot verification succeeds. Imported here (not at the
# top) purely for clarity of ordering; api.storage has no import-time side effects.
from api.storage import Storage  # noqa: E402

Storage.migrated(os.environ["TRENDCUT_DB"]).close()
