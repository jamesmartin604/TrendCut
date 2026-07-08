"""API / integration tests (Phase 6).

Drives the FastAPI app through Starlette's ``TestClient`` against an isolated
in-memory ``Storage`` per test (no shared state, no real DB file). Covers:

* entry CRUD round-trips, weight-only / calorie-only days, upsert-on-duplicate;
* delete reflected in a subsequent ``/estimate`` (proving TDEE is derived, not
  stored);
* settings get/update and that ``/estimate`` respects an updated window/alpha;
* the ``/estimate`` JSON contract — ``insufficient_data`` on a near-empty DB and a
  real estimate once enough days exist, with the plateau verdict always present;
* CSV import → export round-trip, malformed/missing-value handling, upsert of
  existing dates;
* input-guard soft warnings surfacing without rejecting the write.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.bootstrap_user import bootstrap
from api.storage import Storage
from estimator.synthetic import simulate

# Auth is live as of Phase 2, so the data routes now require a session. The default
# client is authenticated: it claims the default user with credentials and logs in,
# and the TestClient carries the session cookie through every subsequent request —
# so the existing data-route tests exercise exactly the same behaviour as before,
# just as an authenticated user. (cookie_secure=False so the cookie survives over
# TestClient's plain-http transport.)
_TEST_EMAIL = "user@test.local"
_TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def client() -> TestClient:
    storage = Storage(":memory:")
    bootstrap(storage, _TEST_EMAIL, _TEST_PASSWORD)
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD})
        assert r.status_code == 200, r.text
        yield c
    storage.close()


def _seed_sufficient(client: TestClient, *, n_days: int = 40) -> list[str]:
    """Upsert a realistic, fully-logged run of days so ``/estimate`` can produce a
    real (non-``insufficient_data``) estimate. Returns the ISO dates written."""
    result = simulate(
        n_days, true_tdee=2500.0, intake=2100.0, seed=7, start_date=_dt.date(2026, 1, 1)
    )
    obs = result.observed
    dates: list[str] = []
    for iso, weight, kcal in zip(obs.dates, obs.weight, obs.intake, strict=True):
        iso = str(iso)
        dates.append(iso)
        client.put(
            f"/entries/{iso}",
            json={"weight_kg": float(weight), "kcal": int(round(kcal))},
        )
    return dates


# --- health --------------------------------------------------------------- #
def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# --- entry CRUD ----------------------------------------------------------- #
def test_entry_crud_roundtrip(client: TestClient) -> None:
    r = client.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": 2000})
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-01-01"
    assert body["weight_kg"] == 80.0
    assert body["kcal"] == 2000
    assert body["warnings"] == []

    got = client.get("/entries/2026-01-01").json()
    assert got["weight_kg"] == 80.0

    assert client.get("/entries/2026-02-02").status_code == 404


def test_weight_only_and_calorie_only_days(client: TestClient) -> None:
    w = client.put("/entries/2026-01-01", json={"weight_kg": 80.0}).json()
    assert w["weight_kg"] == 80.0 and w["kcal"] is None

    c = client.put("/entries/2026-01-02", json={"kcal": 1800}).json()
    assert c["weight_kg"] is None and c["kcal"] == 1800


def test_upsert_on_duplicate_date_overwrites(client: TestClient) -> None:
    client.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": 2000})
    client.put("/entries/2026-01-01", json={"weight_kg": 79.0, "kcal": 1900})

    listed = client.get("/entries").json()
    assert len(listed) == 1
    assert listed[0]["weight_kg"] == 79.0
    assert listed[0]["kcal"] == 1900


def test_list_entries_range(client: TestClient) -> None:
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        client.put(f"/entries/{day}", json={"weight_kg": 80.0})
    ranged = client.get("/entries", params={"start": "2026-01-02", "end": "2026-01-03"}).json()
    assert [e["date"] for e in ranged] == ["2026-01-02", "2026-01-03"]


def test_delete_entry(client: TestClient) -> None:
    client.put("/entries/2026-01-01", json={"weight_kg": 80.0})
    assert client.delete("/entries/2026-01-01").status_code == 204
    assert client.get("/entries/2026-01-01").status_code == 404
    assert client.delete("/entries/2026-01-01").status_code == 404


def test_invalid_date_rejected(client: TestClient) -> None:
    assert client.put("/entries/not-a-date", json={"weight_kg": 80.0}).status_code == 422


# --- settings ------------------------------------------------------------- #
def test_settings_get_and_update(client: TestClient) -> None:
    defaults = client.get("/settings").json()
    assert defaults["alpha"] == 0.10
    assert defaults["window_days"] == 28

    updated = client.put("/settings", json={"window_days": 14, "alpha": 0.2}).json()
    assert updated["window_days"] == 14
    assert updated["alpha"] == 0.2
    # Round-trips through storage.
    assert client.get("/settings").json()["window_days"] == 14


# --- /estimate contract --------------------------------------------------- #
def test_estimate_insufficient_data_on_near_empty_db(client: TestClient) -> None:
    # Two days is nowhere near the ~10-valid-day threshold.
    client.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": 2000})
    client.put("/entries/2026-01-02", json={"weight_kg": 79.9, "kcal": 2000})

    body = client.get("/estimate").json()
    # Shape.
    assert set(body) >= {
        "window_days",
        "alpha",
        "energy_density",
        "n_entries",
        "date_start",
        "date_end",
        "trend",
        "tdee",
        "plateau",
    }
    # Honest declining state — no fabricated number.
    assert body["tdee"]["status"] == "insufficient_data"
    assert body["tdee"]["tdee"] is None
    assert body["tdee"]["ci_low"] is None
    # Plateau verdict is always present; it defers when the estimate can't stand.
    assert body["plateau"]["state"] == "undetermined"
    # The trend is still drawn for whatever points exist (faithful partial state).
    assert len(body["trend"]) == 2
    assert body["n_entries"] == 2


def test_estimate_empty_db(client: TestClient) -> None:
    body = client.get("/estimate").json()
    assert body["n_entries"] == 0
    assert body["trend"] == []
    assert body["date_start"] is None
    assert body["tdee"]["status"] == "insufficient_data"


def test_estimate_real_on_sufficient_data(client: TestClient) -> None:
    _seed_sufficient(client, n_days=40)
    body = client.get("/estimate").json()
    tdee = body["tdee"]
    assert tdee["status"] in {"ok", "low_confidence"}
    assert tdee["tdee"] is not None
    assert tdee["ci_low"] is not None and tdee["ci_high"] is not None
    assert tdee["ci_low"] <= tdee["tdee"] <= tdee["ci_high"]
    # Recovers roughly the hidden 2500 kcal TDEE.
    assert 2200 < tdee["tdee"] < 2800
    assert body["plateau"]["state"] in {"on_track", "slowing", "stalled"}


def test_tdee_history_empty_db(client: TestClient) -> None:
    body = client.get("/tdee-history").json()
    assert set(body) >= {"window_days", "alpha", "energy_density", "points"}
    assert body["points"] == []


def test_tdee_history_shape_and_honest_ramp(client: TestClient) -> None:
    _seed_sufficient(client, n_days=50)
    body = client.get("/tdee-history").json()
    points = body["points"]
    # One point per calendar day in the series.
    assert len(points) == 50
    # Every point carries a status; each has the date/tdee/ci keys.
    for p in points:
        assert set(p) == {"date", "status", "tdee", "ci_low", "ci_high"}
        assert p["status"] in {"ok", "low_confidence", "insufficient_data"}

    # Early days can't support an estimate -> honest insufficient_data, tdee None.
    assert points[0]["status"] == "insufficient_data"
    assert points[0]["tdee"] is None

    # By the end there's a real, bounded estimate.
    last = points[-1]
    assert last["status"] in {"ok", "low_confidence"}
    assert last["tdee"] is not None
    assert last["ci_low"] <= last["tdee"] <= last["ci_high"]

    # No point ever fabricates a number while declaring insufficient_data.
    for p in points:
        if p["status"] == "insufficient_data":
            assert p["tdee"] is None


def test_tdee_history_respects_settings_window(client: TestClient) -> None:
    _seed_sufficient(client, n_days=50)
    client.put("/settings", json={"window_days": 21})
    body = client.get("/tdee-history").json()
    assert body["window_days"] == 21


def test_estimate_respects_updated_window(client: TestClient) -> None:
    _seed_sufficient(client, n_days=40)
    client.put("/settings", json={"window_days": 21, "alpha": 0.15})
    body = client.get("/estimate").json()
    assert body["window_days"] == 21
    assert body["alpha"] == 0.15
    assert body["tdee"]["window_days"] == 21


def test_delete_reflected_in_estimate_proving_derived(client: TestClient) -> None:
    dates = _seed_sufficient(client, n_days=40)
    before = client.get("/estimate").json()["tdee"]["tdee"]
    assert before is not None

    # Remove the most recent weigh-ins; the estimate must recompute, not serve a
    # cached number — proving TDEE is derived from raw entries, never stored.
    for iso in dates[-8:]:
        client.delete(f"/entries/{iso}")
    after = client.get("/estimate").json()["tdee"]["tdee"]
    assert after != before


# --- CSV import / export -------------------------------------------------- #
def test_csv_import_export_roundtrip(client: TestClient) -> None:
    csv_text = "date,weight_kg,kcal\n2026-01-01,80.0,2000\n2026-01-02,79.8,1900\n"
    result = client.post("/entries/import", content=csv_text).json()
    assert result["imported"] == 2

    exported = client.get("/entries/export", params={"format": "csv"}).text
    # Export reproduces the imported rows (canonical header + data).
    assert "date,weight_kg,kcal" in exported
    assert "2026-01-01,80.0,2000" in exported
    assert "2026-01-02,79.8,1900" in exported

    # And re-importing the export is idempotent (upsert, no duplicates).
    client.post("/entries/import", content=exported)
    assert len(client.get("/entries").json()) == 2


def test_csv_import_json_export(client: TestClient) -> None:
    client.post("/entries/import", content="date,weight,calories\n2026-01-01,80,2000\n")
    data = client.get("/entries/export", params={"format": "json"}).json()
    assert len(data) == 1
    assert data[0]["date"] == "2026-01-01"
    assert data[0]["weight_kg"] == 80.0
    assert data[0]["kcal"] == 2000


def test_csv_import_handles_missing_and_malformed(client: TestClient) -> None:
    csv_text = (
        "date,weight_kg,kcal\n"
        "2026-01-01,80.0,\n"  # blank kcal -> None (a gap, not zero)
        "2026-01-02,,1800\n"  # blank weight -> None
        "not-a-date,70,1500\n"  # bad date -> skipped
        "2026-01-03,heavy,1600\n"  # unparseable weight -> treated as missing
    )
    result = client.post("/entries/import", content=csv_text).json()
    assert result["imported"] == 3  # the bad-date row is skipped
    assert any("bad or missing date" in w for w in result["warnings"])
    assert any("weight not a number" in w for w in result["warnings"])

    by_date = {e["date"]: e for e in client.get("/entries").json()}
    assert by_date["2026-01-01"]["kcal"] is None
    assert by_date["2026-01-02"]["weight_kg"] is None
    assert by_date["2026-01-03"]["weight_kg"] is None
    assert "not-a-date" not in by_date


def test_csv_import_many_rows_lands_completely(client: TestClient) -> None:
    """Regression: a multi-row import must succeed in one transaction — the
    per-row-commit loop raised sqlite3 "database is locked" on bulk import."""
    lines = ["date,weight_kg,kcal"]
    lines += [f"2026-02-{i:02d},{80 - i * 0.1:.1f},{1900 + i}" for i in range(1, 13)]
    result = client.post("/entries/import", content="\n".join(lines) + "\n").json()
    assert result["imported"] == 12
    listed = client.get("/entries").json()
    assert len(listed) == 12
    assert listed[0]["date"] == "2026-02-01"
    assert listed[-1]["kcal"] == 1912
    # Single-entry logging keeps working after a bulk import (same connection).
    r = client.put("/entries/2026-02-20", json={"weight_kg": 78.5, "kcal": 1800})
    assert r.status_code == 200
    assert len(client.get("/entries").json()) == 13


def test_csv_import_bad_row_still_commits_good_rows(client: TestClient) -> None:
    """Intended semantics: parser-level bad rows are skipped BEFORE the write, so
    the good rows still import (the batch write itself is all-or-nothing only for
    infrastructure failures, which don't occur here)."""
    csv_text = (
        "date,weight_kg,kcal\n2026-03-01,79.0,1900\ngarbage,heavy,many\n2026-03-03,78.8,1850\n"
    )
    result = client.post("/entries/import", content=csv_text).json()
    assert result["imported"] == 2
    assert any("bad or missing date" in w for w in result["warnings"])
    assert [e["date"] for e in client.get("/entries").json()] == ["2026-03-01", "2026-03-03"]


def test_csv_import_upserts_existing_dates(client: TestClient) -> None:
    client.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": 2000})
    client.post("/entries/import", content="date,weight_kg,kcal\n2026-01-01,78.0,1750\n")
    listed = client.get("/entries").json()
    assert len(listed) == 1
    assert listed[0]["weight_kg"] == 78.0
    assert listed[0]["kcal"] == 1750


# --- input guards (soft warnings) ----------------------------------------- #
def test_out_of_range_weight_warns_without_rejecting(client: TestClient) -> None:
    r = client.put("/entries/2026-01-01", json={"weight_kg": 850.0, "kcal": 2000})
    assert r.status_code == 200  # written, not rejected
    body = r.json()
    assert body["weight_kg"] == 850.0
    assert any("plausible range" in w for w in body["warnings"])
    # And it really was persisted.
    assert client.get("/entries/2026-01-01").json()["weight_kg"] == 850.0


def test_negative_kcal_warns_without_rejecting(client: TestClient) -> None:
    r = client.put("/entries/2026-01-01", json={"weight_kg": 80.0, "kcal": -500})
    assert r.status_code == 200
    assert any("negative" in w for w in r.json()["warnings"])


def test_import_surfaces_guard_warnings(client: TestClient) -> None:
    result = client.post(
        "/entries/import", content="date,weight_kg,kcal\n2026-01-01,850,2000\n"
    ).json()
    assert result["imported"] == 1
    assert any("plausible range" in w for w in result["warnings"])


# --- onboarding: estimate requirements ------------------------------------ #
def test_estimate_requirements_matches_estimator_and_needs_auth() -> None:
    """The onboarding threshold endpoint must report the estimator's *effective*
    minimums. Assert against the estimator's own constants (imported here), never a
    hardcoded 10 — so if the estimator's bar changes, this test catches any drift
    between it and what the onboarding UI tells users."""
    from estimator.tdee import MIN_INTAKE_DAYS, MIN_WEIGHT_DAYS

    storage = Storage(":memory:")
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        # Auth-gated like the rest of the dashboard.
        assert c.get("/estimate/requirements").status_code == 401
        c.post("/auth/register", json={"email": "req@test.local", "password": "password123"})
        r = c.get("/estimate/requirements")
        assert r.status_code == 200
        assert r.json() == {
            "min_weight_days": MIN_WEIGHT_DAYS,
            "min_intake_days": MIN_INTAKE_DAYS,
        }
    storage.close()


# --- registration (public signup) ----------------------------------------- #
def test_register_new_account_then_use_it() -> None:
    """Smoke test for the public /auth/register route: a fresh, unauthenticated
    client can create an account and is immediately authenticated on the data
    routes. (Uses its own storage so it doesn't depend on the pre-seeded fixture.)"""
    storage = Storage(":memory:")
    app = create_app(storage, cookie_secure=False)
    with TestClient(app) as c:
        # No session before signup.
        assert c.get("/settings").status_code == 401
        r = c.post("/auth/register", json={"email": "smoke@test.local", "password": "password123"})
        assert r.status_code == 200
        # Auto-logged-in: the data routes work and the account starts empty.
        assert c.get("/settings").status_code == 200
        assert c.get("/entries").json() == []
    storage.close()
