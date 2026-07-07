/**
 * Tiny API client — fetch wrappers for the endpoints the dashboard needs.
 * Same-origin (the API serves this frontend), so no base URL / CORS concerns.
 */

import { redirectToLogin } from "./auth.js";

/**
 * A 401 means the session is missing or expired: send the user to login rather
 * than surfacing an error on a page they can no longer load. Centralised here so
 * every data call inherits it. Returns true if it handled (redirected).
 */
function handleUnauthorized(res) {
  if (res.status === 401) {
    redirectToLogin();
    return true;
  }
  return false;
}

async function asJson(res) {
  if (!res.ok) {
    if (handleUnauthorized(res)) {
      // Stall the returned promise; the page is navigating away anyway.
      return new Promise(() => {});
    }
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body; keep the status text */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** GET /estimate — trend series + TDEE result + plateau verdict. */
export function getEstimate() {
  return fetch("/estimate").then(asJson);
}

/**
 * GET /tdee-history — rolling TDEE estimate recomputed as-of each past day. Each
 * point carries its own `status`; `tdee`/`ci_*` are null on insufficient_data days.
 */
export function getTdeeHistory() {
  return fetch("/tdee-history").then(asJson);
}

/** GET /settings — estimator knobs + target weight/date. */
export function getSettings() {
  return fetch("/settings").then(asJson);
}

/**
 * PUT /settings — partial update of the estimator/plan knobs (α, window, target
 * weight/date, units, …). Only the keys present are changed. Returns the full
 * settings after the update.
 */
export function updateSettings(patch) {
  return fetch("/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then(asJson);
}

/** GET /entries/{date} — the entry for a day, or null if none exists. */
export async function getEntry(date) {
  const res = await fetch(`/entries/${date}`);
  if (res.status === 404) return null;
  return asJson(res);
}

/**
 * PUT /entries/{date} — create/overwrite the day. `weight_kg` / `kcal` may each
 * be null (weight-only and calorie-only days are allowed). Returns the saved
 * entry including any soft input-guard `warnings`.
 */
export function putEntry(date, { weight_kg, kcal }) {
  return fetch(`/entries/${date}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ weight_kg, kcal }),
  }).then(asJson);
}

/** GET /entries — all entries (each has date, weight_kg, kcal, timestamps). */
export function listEntries() {
  return fetch("/entries").then(asJson);
}

/** DELETE /entries/{date} — permanently remove a day. Resolves on 204. */
export async function deleteEntry(date) {
  const res = await fetch(`/entries/${date}`, { method: "DELETE" });
  if (handleUnauthorized(res)) return new Promise(() => {});
  if (!res.ok && res.status !== 204) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}
