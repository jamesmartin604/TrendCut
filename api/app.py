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
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from api.auth import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    RegistrationError,
    hash_password,
    hash_token,
    new_session_token,
    normalize_email,
    session_expiry,
    validate_registration,
    verify_password,
)
from api.csv_io import entries_to_csv, entries_to_json, parse_entries_csv
from api.estimation import compute_estimate, compute_tdee_history
from api.guards import entry_warnings
from api.models import (
    AuthMeResponse,
    EntryResponse,
    EntryUpsert,
    EstimateResponse,
    ImportResult,
    LoginRequest,
    RegisterRequest,
    SettingsResponse,
    SettingsUpdate,
    TdeeHistoryResponse,
)
from api.storage import Entry, Storage


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def get_storage(request: Request) -> Storage:
    """Dependency: the app's Storage instance (overridden in tests)."""
    return request.app.state.storage


def get_current_user_id(request: Request, store: Storage = Depends(get_storage)) -> int:
    """Resolve the authenticated user from the session cookie, or raise 401.

    This is the auth seam Phase 1 set up: every data route depends on it and
    already threads the returned ``user_id`` through storage, so authentication is
    concentrated here. The cookie carries an opaque token; only its SHA-256 hash is
    stored, so we hash the presented token and look up a non-expired session.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user_id = store.get_session_user_id(hash_token(token), _now_iso())
        if user_id is not None:
            return user_id
    raise HTTPException(status_code=401, detail="Not authenticated")


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


def _resolve_cookie_secure(cookie_secure: bool | None) -> bool:
    """Whether the session cookie gets the ``Secure`` flag.

    Defaults to secure (production-safe). Local dev over plain HTTP must relax it
    (a ``Secure`` cookie is never sent over http://localhost, so login would appear
    to "not work") via ``cookie_secure=False`` or ``TRENDCUT_COOKIE_SECURE=0``. This
    is an explicit switch rather than sniffing the request scheme, so a
    misconfigured proxy can't silently drop ``Secure`` in production.
    """
    if cookie_secure is not None:
        return cookie_secure
    return os.environ.get("TRENDCUT_COOKIE_SECURE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def create_app(storage: Storage | None = None, *, cookie_secure: bool | None = None) -> FastAPI:
    """Build the app. Pass a Storage (e.g. in-memory) for tests; otherwise one is
    created from the ``TRENDCUT_DB`` env var (default ``trendcut.db``).

    ``cookie_secure`` controls the session cookie's ``Secure`` flag (see
    :func:`_resolve_cookie_secure`); tests and local HTTP dev pass ``False``.
    """
    app = FastAPI(title="TrendCut API", version="0.1.0")
    app.state.storage = storage or Storage(os.environ.get("TRENDCUT_DB", "trendcut.db"))
    cookie_secure = _resolve_cookie_secure(cookie_secure)

    # --- auth -------------------------------------------------------------- #
    # These routes must NOT depend on get_current_user_id (you can't require a
    # session to log in / register). Registered before the static mount like every
    # API route.
    def issue_session(response: Response, store: Storage, user_id: int) -> None:
        """Create a session and set the cookie. Shared by login and register so both
        produce byte-for-byte identical session cookies."""
        token = new_session_token()
        store.create_session(hash_token(token), user_id, session_expiry())
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
            httponly=True,
            secure=cookie_secure,
            samesite="lax",
            path="/",
        )

    @app.post("/auth/login")
    def login(
        body: LoginRequest,
        response: Response,
        store: Storage = Depends(get_storage),
    ) -> dict[str, bool]:
        store.delete_expired_sessions(_now_iso())
        # Normalize identically to registration so casing/whitespace resolve to the
        # same account (see api.auth.normalize_email).
        user = store.get_user_by_email(normalize_email(body.email))
        # Constant-time: verify_password runs a dummy argon2 verify when there is no
        # user / no hash, so an unknown email and a wrong password are
        # indistinguishable by timing. The error text is identical either way — no
        # account-existence oracle.
        password_hash = user.password_hash if user is not None else None
        if not verify_password(password_hash, body.password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        issue_session(response, store, user.id)
        return {"ok": True}

    @app.post("/auth/register")
    def register(
        body: RegisterRequest,
        response: Response,
        store: Storage = Depends(get_storage),
    ) -> dict[str, bool]:
        # Open signup, no email verification (see README). Validation is minimal and
        # authoritative here; the client mirror is UX only.
        try:
            email = validate_registration(body.email, body.password)
        except RegistrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.delete_expired_sessions(_now_iso())
        password_hash = hash_password(body.password)
        # The UNIQUE-email index is the real duplicate guard: create the account and
        # catch a conflict, rather than check-then-insert (which races). The insert
        # is atomic, so a rejected duplicate leaves no partial account.
        try:
            user_id = store.create_credentialed_user(email, password_hash)
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409, detail="That email is already registered."
            ) from None
        # Auto-login: land the new user authenticated, exactly as login does.
        issue_session(response, store, user_id)
        return {"ok": True}

    @app.post("/auth/logout")
    def logout(
        request: Request,
        response: Response,
        store: Storage = Depends(get_storage),
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            store.delete_session(hash_token(token))
        store.delete_expired_sessions(_now_iso())
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/auth/me", response_model=AuthMeResponse)
    def auth_me(request: Request, store: Storage = Depends(get_storage)) -> AuthMeResponse:
        # Intentionally does NOT 401 — it's the frontend's is-logged-in probe.
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            user_id = store.get_session_user_id(hash_token(token), _now_iso())
            if user_id is not None:
                user = store.get_user(user_id)
                return AuthMeResponse(authenticated=True, email=user.email if user else None)
        return AuthMeResponse(authenticated=False)

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
