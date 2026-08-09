/**
 * Calendar-date helpers. The point of these tests is the year-disambiguation rule
 * that fixed the target-projection card: it rendered
 *
 *     "Projected 74.0 kg by Jul 9 — after your Aug 9 target."
 *
 * The comparison behind that sentence was correct — the arrival really was
 * 2027-07-09, genuinely after the 2026-08-09 target — and only the display dropped
 * the year. Run with `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { addDays, fmtDateHuman, needsYear } from "../dates.js";

const utc = (iso) => new Date(`${iso}T00:00:00Z`);
const NOW_2026 = utc("2026-08-09");

test("fmtDateHuman omits the year by default", () => {
  assert.equal(fmtDateHuman(utc("2026-07-09")), "Jul 9");
  assert.equal(fmtDateHuman(utc("2026-12-31")), "Dec 31");
});

test("fmtDateHuman includes the year on request", () => {
  assert.equal(fmtDateHuman(utc("2027-07-09"), { withYear: true }), "Jul 9, 2027");
  assert.equal(fmtDateHuman(utc("2026-08-09"), { withYear: true }), "Aug 9, 2026");
});

test("[regression] year-less formatting collapses different years together", () => {
  // The defect itself: three distinct dates, one indistinguishable string. This is
  // why the projection card needs `withYear`, not why the formatter is wrong.
  const same = ["2026-07-09", "2027-07-09", "2031-07-09"].map((d) => fmtDateHuman(utc(d)));
  assert.deepEqual(same, ["Jul 9", "Jul 9", "Jul 9"]);
});

test("fmtDateHuman is UTC-stable regardless of the viewer's timezone", () => {
  // A calendar day must not shift for a viewer west of UTC.
  assert.equal(fmtDateHuman(new Date("2026-07-09T00:30:00Z")), "Jul 9");
  assert.equal(fmtDateHuman(new Date("2026-07-09T23:30:00Z")), "Jul 9");
});

test("needsYear: false when every date is in the current year", () => {
  assert.equal(needsYear([utc("2026-01-01")], NOW_2026), false);
  assert.equal(needsYear([utc("2026-09-10"), utc("2026-08-09")], NOW_2026), false);
  assert.equal(needsYear([utc("2026-12-31")], NOW_2026), false);
});

test("needsYear: true for a next-year date", () => {
  assert.equal(needsYear([utc("2027-01-01")], NOW_2026), true);
  assert.equal(needsYear([utc("2027-07-09")], NOW_2026), true);
});

test("needsYear: true for a far-future date", () => {
  assert.equal(needsYear([utc("2033-07-09")], NOW_2026), true);
});

test("needsYear: true for a past year too (a target already behind you)", () => {
  assert.equal(needsYear([utc("2025-12-31")], NOW_2026), true);
});

test("needsYear decides JOINTLY — one out-of-year date years-ifies the whole group", () => {
  // The projected arrival leaves the year but the target has not: both must still be
  // formatted the same way, or the bare date silently implies the wrong year.
  assert.equal(needsYear([utc("2027-07-09"), utc("2026-08-09")], NOW_2026), true);
  // …and symmetrically, when the TARGET is the far date.
  assert.equal(needsYear([utc("2026-09-10"), utc("2027-08-09")], NOW_2026), true);
});

test("needsYear tolerates a missing date (no target set)", () => {
  assert.equal(needsYear([utc("2026-09-10"), null], NOW_2026), false);
  assert.equal(needsYear([utc("2027-09-10"), null], NOW_2026), true);
  assert.equal(needsYear([utc("2026-09-10"), undefined], NOW_2026), false);
  assert.equal(needsYear([], NOW_2026), false);
  assert.equal(needsYear(null, NOW_2026), false);
});

test("[adversarial] needsYear ignores an invalid date rather than throwing", () => {
  assert.equal(needsYear([new Date("nonsense"), utc("2026-09-10")], NOW_2026), false);
});

test("addDays crosses month and year boundaries in UTC", () => {
  assert.equal(addDays("2026-08-09", 1).toISOString().slice(0, 10), "2026-08-10");
  assert.equal(addDays("2026-08-31", 1).toISOString().slice(0, 10), "2026-09-01");
  assert.equal(addDays("2026-12-31", 1).toISOString().slice(0, 10), "2027-01-01");
  assert.equal(addDays("2026-08-08", 335).toISOString().slice(0, 10), "2027-07-09");
  assert.equal(addDays("2026-03-01", -1).toISOString().slice(0, 10), "2026-02-28");
  // Leap day survives the arithmetic.
  assert.equal(addDays("2028-02-28", 1).toISOString().slice(0, 10), "2028-02-29");
});

// ---------- the projection sentence, end to end ---------- //

/** Rebuilds the projection card's two-date sentence exactly as `renderPace` does. */
function projectionSentence(arrival, wanted, now) {
  const withYear = needsYear([arrival, wanted], now);
  const arrivalText = fmtDateHuman(arrival, { withYear });
  if (!wanted) return `Projected to reach 74.0 kg by ${arrivalText}.`;
  return arrival <= wanted
    ? `On pace for 74.0 kg by ${arrivalText}.`
    : `Projected 74.0 kg by ${arrivalText} — after your ` +
        `${fmtDateHuman(wanted, { withYear })} target.`;
}

test("[regression] the reported card no longer contradicts itself", () => {
  const s = projectionSentence(utc("2027-07-09"), utc("2026-08-09"), NOW_2026);
  // Before the fix this read "…by Jul 9 — after your Aug 9 target", which is false
  // on its face. The comparison was always right; only the rendering hid the year.
  assert.equal(s, "Projected 74.0 kg by Jul 9, 2027 — after your Aug 9, 2026 target.");
  assert.ok(!/by Jul 9 —/.test(s), "the bare-date form must not survive");
});

test("a same-year projection stays clean (no gratuitous years)", () => {
  const s = projectionSentence(utc("2026-09-10"), utc("2026-08-09"), NOW_2026);
  assert.equal(s, "Projected 74.0 kg by Sep 10 — after your Aug 9 target.");
  assert.ok(!/2026/.test(s), "an in-year sentence must not gain a year");
});

test("an on-pace projection is year-disambiguated too", () => {
  const s = projectionSentence(utc("2027-01-15"), utc("2027-03-01"), NOW_2026);
  assert.equal(s, "On pace for 74.0 kg by Jan 15, 2027.");
});

test("the TARGET's year drives the decision when the arrival is in-year", () => {
  // The other half of the fix, with both dates actually rendered: a target date that
  // has already passed. The arrival is this year, the target is not, and the joint
  // rule must year-ify both rather than leaving "Dec 1" reading as a future date.
  const s = projectionSentence(utc("2026-09-10"), utc("2025-12-01"), NOW_2026);
  assert.equal(s, "Projected 74.0 kg by Sep 10, 2026 — after your Dec 1, 2025 target.");
});

test("no target set: the lone arrival still gets its year when it needs one", () => {
  assert.equal(
    projectionSentence(utc("2027-07-09"), null, NOW_2026),
    "Projected to reach 74.0 kg by Jul 9, 2027.",
  );
  assert.equal(
    projectionSentence(utc("2026-09-10"), null, NOW_2026),
    "Projected to reach 74.0 kg by Sep 10.",
  );
});
