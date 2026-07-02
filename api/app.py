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

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from api.csv_io import entries_to_csv, entries_to_json, parse_entries_csv
from api.estimation import compute_estimate
from api.guards import entry_warnings
from api.models import (
    EntryResponse,
    EntryUpsert,
    EstimateResponse,
    ImportResult,
    SettingsResponse,
    SettingsUpdate,
)
from api.storage import Entry, Storage


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
    async def import_csv(request: Request, store: Storage = Depends(get_storage)) -> ImportResult:
        text = (await request.body()).decode("utf-8-sig")
        rows, warnings = parse_entries_csv(text)
        for row in rows:
            warnings.extend(f"{row.date}: {w}" for w in entry_warnings(row.weight_kg, row.kcal))
            store.upsert_entry(row.date, row.weight_kg, row.kcal)
        return ImportResult(imported=len(rows), warnings=warnings)

    @app.get("/entries/export")
    def export(format: str = "csv", store: Storage = Depends(get_storage)) -> Response:
        entries = store.list_entries()
        if format == "json":
            return JSONResponse(content=entries_to_json(entries))
        if format == "csv":
            return PlainTextResponse(content=entries_to_csv(entries), media_type="text/csv")
        raise HTTPException(status_code=422, detail="format must be 'csv' or 'json'")

    # --- entries ----------------------------------------------------------- #
    @app.put("/entries/{date}", response_model=EntryResponse)
    def upsert_entry(
        date: str, body: EntryUpsert, store: Storage = Depends(get_storage)
    ) -> EntryResponse:
        iso = _validate_date(date)
        warnings = entry_warnings(body.weight_kg, body.kcal)
        entry = store.upsert_entry(iso, body.weight_kg, body.kcal)
        return _entry_response(entry, warnings)

    @app.get("/entries/{date}", response_model=EntryResponse)
    def get_entry(date: str, store: Storage = Depends(get_storage)) -> EntryResponse:
        iso = _validate_date(date)
        entry = store.get_entry(iso)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no entry for {iso}")
        return _entry_response(entry)

    @app.get("/entries", response_model=list[EntryResponse])
    def list_entries(
        start: str | None = None,
        end: str | None = None,
        store: Storage = Depends(get_storage),
    ) -> list[EntryResponse]:
        start_iso = _validate_date(start) if start else None
        end_iso = _validate_date(end) if end else None
        entries = store.list_entries(start_iso, end_iso)
        return [_entry_response(e) for e in entries]

    @app.delete("/entries/{date}")
    def delete_entry(date: str, store: Storage = Depends(get_storage)) -> Response:
        iso = _validate_date(date)
        if not store.delete_entry(iso):
            raise HTTPException(status_code=404, detail=f"no entry for {iso}")
        return Response(status_code=204)

    # --- settings ---------------------------------------------------------- #
    @app.get("/settings", response_model=SettingsResponse)
    def get_settings(store: Storage = Depends(get_storage)) -> SettingsResponse:
        return SettingsResponse(**vars(store.get_settings()))

    @app.put("/settings", response_model=SettingsResponse)
    def update_settings(
        body: SettingsUpdate, store: Storage = Depends(get_storage)
    ) -> SettingsResponse:
        updated = store.update_settings(**body.model_dump(exclude_unset=True))
        return SettingsResponse(**vars(updated))

    # --- estimate (pure read + compute; never stored) ---------------------- #
    @app.get("/estimate", response_model=EstimateResponse)
    def estimate(store: Storage = Depends(get_storage)) -> EstimateResponse:
        return compute_estimate(store.list_entries(), store.get_settings())

    return app


app = create_app()
