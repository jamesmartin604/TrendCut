/**
 * Calendar-date helpers — pure, no DOM, so the year-disambiguation rules are
 * unit-testable (the same discipline as `status.js` / `derivation.js`).
 *
 * Everything here works in **UTC**. Dates in this app are calendar days, not
 * instants: an entry logged on "2026-07-09" is that day everywhere, so formatting
 * and arithmetic must never let a viewer's timezone shift the day.
 *
 * Why the year is conditional
 * ---------------------------
 * Most dates the dashboard shows are logged days — recent, unambiguous, and cleaner
 * without a year. But the target-projection card renders two dates that can be
 * arbitrarily far out: the projected arrival (unbounded — a shallow trend projects
 * years ahead) and the user's own target date. Formatting those year-less produced a
 * self-contradictory card:
 *
 *     "Projected 74.0 kg by Jul 9 — after your Aug 9 target."
 *
 * The comparison behind that sentence was correct (it used full dates, and the
 * arrival really was 2027-07-09, genuinely after the 2026-08-09 target); only the
 * display dropped the year. Hence `withYear`, decided jointly for a sentence via
 * `needsYear` so the year never appears on one half and not the other.
 */

/** `iso` ("YYYY-MM-DD") advanced by `days`, as a UTC Date. */
export function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d;
}

/**
 * Short human date, e.g. "Jul 9" — or "Jul 9, 2027" when `withYear` is set.
 *
 * @param {Date} date
 * @param {{withYear?: boolean}} [opts]
 */
export function fmtDateHuman(date, { withYear = false } = {}) {
  return date.toLocaleString("en", {
    month: "short",
    day: "numeric",
    ...(withYear ? { year: "numeric" } : {}),
    timeZone: "UTC",
  });
}

/**
 * Should these dates be shown with their year?
 *
 * True when ANY of them falls outside the current year — decided across the whole
 * group so every date in one sentence is formatted the same way. A lone out-of-year
 * date would otherwise read as "Jul 9, 2027 — after your Aug 9 target", where the
 * bare second date silently implies a different year to the first.
 *
 * Ignores null/undefined entries, so a caller can pass an optional date without
 * branching. Comparison is in UTC, like every other calendar operation here; near
 * New Year that can add a year a viewer might consider unnecessary, which is the
 * harmless direction to err.
 *
 * @param {Array<Date|null|undefined>} dates
 * @param {Date} [now] injectable for tests; defaults to the current instant.
 */
export function needsYear(dates, now = new Date()) {
  const currentYear = now.getUTCFullYear();
  return (dates ?? []).some(
    (d) => d != null && !Number.isNaN(d.getTime()) && d.getUTCFullYear() !== currentYear,
  );
}
