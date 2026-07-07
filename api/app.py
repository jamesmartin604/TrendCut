"""FastAPI application — thin routes that validate and delegate.

Handlers contain no business logic: entry/settings persistence lives in
:mod:`api.storage`, the estimate computation in :mod:`api.estimation`, CSV parsing
in :mod:`api.csv_io`, and plausibility checks in :mod:`api.guards`.

Run locally::

    uvicorn api.app:app --reload
    # DB path defaults to ./trendcut.db; override with TRENDCUT_DB=/path/to.db

Build with an explicit storage (tests use in-memory) via :func:`create_app`.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from api.csv_io import entries_to_csv, entries_to_json, parse_entries_csv
from api.estimation import compute_estimate, compute_tdee_history
from api.guards import entry_warnings
from api.models import (
    EntryResponse,
    EntryUpsert,
    EstimateResponse,
    ImportResult,
    SettingsResponse,
    SettingsUpdate,
    TdeeHistoryResponse,
)
from api.storage import DEFAULT_USER_ID, Entry, Storage


def get_current_user_id() -> int:
    """Resolve the user making this request.

    PHASE-2 AUTH INSERTION POINT. The storage layer is fully user-scoped, but the
    HTTP surface is still single-user, so every request is attributed to the
    seeded default user. Phase 2 replaces ONLY this dependency's body with real
    authentication (session/token -> user id); no handler or storage change is
    needed because they already thread ``user_id`` explicitly.
    """
    return DEFAULT_USER_ID


def _validate_date(date: str) -> str:
    try:
        return _dt.date.fromisoformat(date).isoformat()
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"date must be ISO YYYY-MM-DD, got {date!r}"
        ) from exc


def _entry_response(entry: Entry, warnings: list[str] | None = None) -> EntryResponse:
    return EntryResponse(
        date=entry.date,
        weight_kg=entry.weight_kg,
        kcal=entry.kcal,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        warnings=warnings or [],
    )


def get_storage(request: Request) -> Storage:
    """Dependency: the app's Storage instance (overridden in tests)."""
    return request.app.state.storage


class _NoCacheStaticFiles(StaticFiles):
    """Serve the frontend with ``Cache-Control: no-cache``.

    Starlette's StaticFiles sends only ``ETag``/``Last-Modified``; with no
    ``Cache-Control`` a browser may *heuristically* reuse a cached asset without
    revalidating, so a stale ``app.css``/JS can render against freshly-changed HTML
    (e.g. right after a redeploy) until a manual hard refresh. ``no-cache`` keeps the
    files cacheable but forces revalidation — an unchanged file is a cheap ``304``,
    a changed one is fetched — so updates are always picked up automatically.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(storage: Storage | None = None) -> FastAPI:
    """Build the app. Pass a Storage (e.g. in-memory) for tests; otherwise one is
    created from the ``TRENDCUT_DB`` env var (default ``trendcut.db``)."""
    app = FastAPI(title="TrendCut API", version="0.1.0")
    app.state.storage = storage or Storage(os.environ.get("TRENDCUT_DB", "trendcut.db"))

    # --- CSV import / export ---------------------------------------------- #
    # Registered before the parametrised ``/entries/{date}`` routes so the literal
    # ``/entries/import`` and ``/entries/export`` paths match first (FastAPI matches
    # routes in declaration order; otherwise ``{date}`` would capture "export").
    @app.post("/entries/import", response_model=ImportResult)
    async def import_csv(
        request: Request,
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> ImportResult:
        text = (await request.body()).decode("utf-8-sig")
        # The user's display-units setting resolves a unit-less weight header (the
        # parser warns whenever that assumption is used).
        rows, warnings = parse_entries_csv(text, assumed_units=store.get_settings(user_id).units)
        # Import upserts, so a date that already exists gets overwritten — count
        # those before writing and surface it (the UI warns pre-commit; this
        # confirms what actually happened).
        existing = {e.date for e in store.list_entries(user_id)}
        overwritten = len({row.date for row in rows} & existing)
        for row in rows:
            warnings.extend(f"{row.date}: {w}" for w in entry_warnings(row.weight_kg, row.kcal))
        # All rows in ONE transaction (atomic, and no per-row commit churn — the
        # per-row upsert loop was the "database is locked" trigger on import).
        store.upsert_entries(user_id, [(row.date, row.weight_kg, row.kcal) for row in rows])
        if overwritten:
            warnings.append(
                f"{overwritten} existing entr{'y was' if overwritten == 1 else 'ies were'} "
                f"overwritten with the file's values."
            )
        return ImportResult(imported=len(rows), warnings=warnings)

    @app.get("/entries/export")
    def export(
        format: str = "csv",
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> Response:
        entries = store.list_entries(user_id)
        if format == "json":
            return JSONResponse(content=entries_to_json(entries))
        if format == "csv":
            return PlainTextResponse(content=entries_to_csv(entries), media_type="text/csv")
        raise HTTPException(status_code=422, detail="format must be 'csv' or 'json'")

    # --- entries ----------------------------------------------------------- #
    @app.put("/entries/{date}", response_model=EntryResponse)
    def upsert_entry(
        date: str,
        body: EntryUpsert,
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> EntryResponse:
        iso = _validate_date(date)
        warnings = entry_warnings(body.weight_kg, body.kcal)
        entry = store.upsert_entry(user_id, iso, body.weight_kg, body.kcal)
        return _entry_response(entry, warnings)

    @app.get("/entries/{date}", response_model=EntryResponse)
    def get_entry(
        date: str,
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> EntryResponse:
        iso = _validate_date(date)
        entry = store.get_entry(user_id, iso)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no entry for {iso}")
        return _entry_response(entry)

    @app.get("/entries", response_model=list[EntryResponse])
    def list_entries(
        start: str | None = None,
        end: str | None = None,
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> list[EntryResponse]:
        start_iso = _validate_date(start) if start else None
        end_iso = _validate_date(end) if end else None
        entries = store.list_entries(user_id, start_iso, end_iso)
        return [_entry_response(e) for e in entries]

    @app.delete("/entries/{date}")
    def delete_entry(
        date: str,
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> Response:
        iso = _validate_date(date)
        if not store.delete_entry(user_id, iso):
            raise HTTPException(status_code=404, detail=f"no entry for {iso}")
        return Response(status_code=204)

    # --- settings ---------------------------------------------------------- #
    @app.get("/settings", response_model=SettingsResponse)
    def get_settings(
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> SettingsResponse:
        return SettingsResponse(**vars(store.get_settings(user_id)))

    @app.put("/settings", response_model=SettingsResponse)
    def update_settings(
        body: SettingsUpdate,
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> SettingsResponse:
        updated = store.update_settings(user_id, **body.model_dump(exclude_unset=True))
        return SettingsResponse(**vars(updated))

    # --- estimate (pure read + compute; never stored) ---------------------- #
    @app.get("/estimate", response_model=EstimateResponse)
    def estimate(
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> EstimateResponse:
        return compute_estimate(store.list_entries(user_id), store.get_settings(user_id))

    @app.get("/tdee-history", response_model=TdeeHistoryResponse)
    def tdee_history(
        store: Storage = Depends(get_storage),
        user_id: int = Depends(get_current_user_id),
    ) -> TdeeHistoryResponse:
        return compute_tdee_history(store.list_entries(user_id), store.get_settings(user_id))

    # --- health ------------------------------------------------------------- #
    # Liveness probe for the deploy platform (Fly). Trivial 200 + JSON; touches no
    # storage so it stays green even on an empty/fresh volume.
    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    # --- frontend ----------------------------------------------------------- #
    # Static mount only (no business logic). Mounted last so the API routes above
    # always match first; `html=True` serves web/index.html at "/". Served with
    # no-cache so edits/redeploys don't leave a stale stylesheet cached.
    web_dir = Path(__file__).resolve().parent.parent / "web"
    app.mount("/", _NoCacheStaticFiles(directory=web_dir, html=True))

    return app


app = create_app()
