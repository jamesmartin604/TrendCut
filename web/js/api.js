/**
 * Tiny API client — fetch wrappers for the endpoints the dashboard needs.
 * Same-origin (the API serves this frontend), so no base URL / CORS concerns.
 */

async function asJson(res) {
  if (!res.ok) {
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

/** GET /settings — estimator knobs + target weight/date. */
export function getSettings() {
  return fetch("/settings").then(asJson);
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
