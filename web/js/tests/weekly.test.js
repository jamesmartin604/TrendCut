/**
 * Weekly aggregation — ISO-week bucketing (Mon→Sun), averages, days-logged, the
 * trend-based net change, and the honest "no estimate this week" case. Run with
 * `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { addDaysIso, aggregateWeeks, mean, weekStart } from "../weekly.js";

const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

// ---------- week boundary (documented: ISO week, Monday) ---------- //

test("weekStart returns the Monday (UTC) of the ISO week", () => {
  // 2026-01-05 is a Monday. Every day Mon..Sun maps back to it; the next Monday flips.
  for (const iso of ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-11"]) {
    assert.equal(weekStart(iso), "2026-01-05");
  }
  assert.equal(weekStart("2026-01-12"), "2026-01-12");
  assert.equal(weekStart("2026-01-04"), "2025-12-29"); // Sunday belongs to the prior week
});

test("addDaysIso shifts a date in UTC", () => {
  assert.equal(addDaysIso("2026-01-05", 6), "2026-01-11"); // Mon → Sun
  assert.equal(addDaysIso("2026-01-01", -1), "2025-12-31");
});

test("mean ignores missing values; empty → null", () => {
  assert.ok(near(mean([2, 4, 6]), 4));
  assert.ok(near(mean([2, null, 6, NaN]), 4));
  assert.equal(mean([]), null);
  assert.equal(mean([null, NaN]), null);
});

// ---------- aggregation ---------- //

// A full ISO week (Mon 2026-01-05 .. Sun 2026-01-11) plus one day of the next week.
const entries = [
  { date: "2026-01-05", weight_kg: 84.0, kcal: 2000 },
  { date: "2026-01-06", weight_kg: 83.6, kcal: 2200 },
  { date: "2026-01-08", weight_kg: 83.4, kcal: null }, // weight-only day, and a gap on the 7th
  { date: "2026-01-11", weight_kg: 83.0, kcal: 1800 },
  { date: "2026-01-12", weight_kg: 82.8, kcal: 2100 }, // next week (Monday)
];
const trendByDate = {
  "2026-01-05": 84.0,
  "2026-01-06": 83.8,
  "2026-01-08": 83.5,
  "2026-01-11": 83.2,
  "2026-01-12": 83.1,
};
const tdeeByDate = {
  "2026-01-06": { tdee: 2500 },
  "2026-01-11": { tdee: 2450 }, // latest estimate in the first week
  "2026-01-12": { tdee: 2440 },
};

test("aggregateWeeks buckets by ISO week, newest first", () => {
  const weeks = aggregateWeeks(entries, trendByDate, tdeeByDate);
  assert.equal(weeks.length, 2);
  assert.equal(weeks[0].weekStart, "2026-01-12"); // newest first
  assert.equal(weeks[1].weekStart, "2026-01-05");
  assert.equal(weeks[1].weekEnd, "2026-01-11");
});

test("aggregateWeeks: averages, days-logged, and trend net change", () => {
  const w = aggregateWeeks(entries, trendByDate, tdeeByDate)[1]; // the full week
  assert.equal(w.logged, 4); // 4 of 7 days logged (missing 7th, 9th, 10th)
  assert.ok(near(w.avgWeightKg, (84.0 + 83.6 + 83.4 + 83.0) / 4));
  assert.ok(near(w.avgIntake, (2000 + 2200 + 1800) / 3)); // kcal-less day excluded
  assert.ok(near(w.avgTrendKg, (84.0 + 83.8 + 83.5 + 83.2) / 4));
  // Net change = last available trend − first available trend within the week.
  assert.ok(near(w.netChangeKg, 83.2 - 84.0));
  // Weekly TDEE = the most recent rolling estimate the estimator produced that week.
  assert.equal(w.weeklyTdee, 2450);
  assert.ok(near(w.deficit, 2450 - (2000 + 2200 + 1800) / 3));
});

test("[adversarial] a week with a single logged day: no net change, but averages hold", () => {
  const w = aggregateWeeks(
    [{ date: "2026-01-05", weight_kg: 84, kcal: 2000 }],
    { "2026-01-05": 84 },
    {},
  )[0];
  assert.equal(w.logged, 1);
  assert.ok(near(w.avgWeightKg, 84));
  assert.equal(w.netChangeKg, null); // fewer than 2 trend points → honest null, not 0
});

test("[adversarial / honest] no estimate this week → weeklyTdee & deficit stay null (never fabricated)", () => {
  // Calorie-only entries, no trend, no rolling estimate available.
  const w = aggregateWeeks(
    [
      { date: "2026-01-05", weight_kg: null, kcal: 2000 },
      { date: "2026-01-07", weight_kg: null, kcal: 2100 },
    ],
    {}, // no trend for these days
    {}, // estimator declined all week
  )[0];
  assert.equal(w.avgWeightKg, null); // all-missing weights → null, not NaN
  assert.equal(w.avgTrendKg, null);
  assert.equal(w.netChangeKg, null);
  assert.equal(w.weeklyTdee, null);
  assert.equal(w.deficit, null);
  assert.ok(near(w.avgIntake, 2050)); // intake still averages honestly
});

test("[adversarial] no entries → no weeks", () => {
  assert.deepEqual(aggregateWeeks([]), []);
});
