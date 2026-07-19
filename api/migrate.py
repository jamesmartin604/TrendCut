"""Explicit schema migration — the ONLY thing that mutates a DB's schema.

The app no longer migrates on boot (that silently rewrote the live volume before a
snapshot could be taken); boot now *verifies* the schema and refuses to start on a
mismatch. This CLI is the deliberate, separately-invoked step that brings a database
up to the schema the code expects:

    python -m api.migrate                      # migrates $TRENDCUT_DB (default trendcut.db)
    python -m api.migrate --db /data/trendcut.db

It is idempotent — running it on an already-current DB is a no-op. On the live Fly
deploy, **snapshot the volume first** (``flyctl volumes snapshots create``) and make
sure nothing else holds the DB open (a stray ``--reload`` process, DB Browser).

First local run: a fresh checkout has no schema, so run this once before the first
boot (``uvicorn api.app:app`` will otherwise refuse to start and tell you to).
"""

from __future__ import annotations

import argparse
import os
import sys

from api.storage import Storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m api.migrate",
        description="Create/upgrade the database schema to what the code expects. "
        "Idempotent; safe to re-run. Boot does NOT do this — it only verifies.",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TRENDCUT_DB", "trendcut.db"),
        help="SQLite path (default: $TRENDCUT_DB or trendcut.db)",
    )
    args = parser.parse_args(argv)

    store = Storage(args.db)
    try:
        before = store.verify_schema()
        if before:
            print(f"{args.db}: schema needs updating —")
            for problem in before:
                print(f"  - {problem}")
        else:
            print(f"{args.db}: schema already current — migrate is a no-op.")

        store.migrate()

        after = store.verify_schema()
        if after:
            # Should never happen: a successful migrate must satisfy verify_schema.
            print("Migration did not produce the expected schema:", file=sys.stderr)
            for problem in after:
                print(f"  - {problem}", file=sys.stderr)
            return 1
    finally:
        store.close()

    print(f"{args.db}: schema is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
