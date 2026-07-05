/**
 * Pure chart-math tests — coordinate mapping + inverse, domain/tick computation, and
 * the goal-line logic. Run with `node --test`. No DOM, no fetch: these import the
 * same pure functions the browser draw code uses.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  daysBetween,
  definedRange,
  extent,
  goalLineAnchors,
  goalWeightAt,
  indexDomain,
  linePath,
  linearScale,
  nearestIndex,
  padDomain,
  round1,
  ticks,
  weekStartIndices,
  xLabelIndices,
} from "../chart-math.js";

const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

// ---------- 1. coordinate mapping: value → pixel and its inverse ---------- //

test("linearScale maps domain endpoints onto range endpoints", () => {
  const s = linearScale(0, 10, 100, 200);
  assert.ok(near(s(0), 100));
  assert.ok(near(s(10), 200));
  assert.ok(near(s(5), 150)); // midpoint
});

test("linearScale ⇄ nearestIndex round-trips every index", () => {
  const n = 7;
  const x0 = 44;
  const x1 = 620;
  const xScale = linearScale(0, n - 1, x0, x1);
  for (let i = 0; i < n; i += 1) {
    assert.equal(nearestIndex(xScale(i), x0, x1, n), i);
  }
});

test("nearestIndex snaps to the closest column between points", () => {
  const n = 5;
  const x0 = 0;
  const x1 = 400; // 100px per step
  // Just past the mid-point between index 2 (200px) and 3 (300px) rounds to 3.
  assert.equal(nearestIndex(251, x0, x1, n), 3);
  assert.equal(nearestIndex(249, x0, x1, n), 2);
});

test("nearestIndex clamps off-plot cursors to the nearest end", () => {
  assert.equal(nearestIndex(-500, 0, 400, 5), 0);
  assert.equal(nearestIndex(9999, 0, 400, 5), 4);
});

test("nearestIndex uses the PASSED-IN width (stale dimensions are catchable)", () => {
  // Same cursor pixel, two different chart widths → two different data indices.
  const px = 300;
  assert.equal(nearestIndex(px, 0, 600, 7), 3); // 100px/step → index 3
  assert.equal(nearestIndex(px, 0, 300, 7), 6); // 50px/step  → index 6 (right edge)
});

test("[adversarial] nearestIndex on an empty series returns -1", () => {
  assert.equal(nearestIndex(123, 0, 400, 0), -1);
});

test("[adversarial] nearestIndex with a single point always returns 0", () => {
  assert.equal(nearestIndex(0, 0, 400, 1), 0);
  assert.equal(nearestIndex(400, 0, 400, 1), 0);
});

test("linearScale with a degenerate domain parks values mid-range (no divide-by-zero)", () => {
  const s = linearScale(5, 5, 100, 200);
  assert.ok(near(s(5), 150));
  assert.ok(near(s(999), 150));
});

// ---------- 2. domain / tick computation ---------- //

test("extent ignores null/NaN and returns [lo, hi]", () => {
  assert.deepEqual(extent([3, null, 1, NaN, 2]), [1, 3]);
});

test("[adversarial] extent of empty / all-missing input is null", () => {
  assert.equal(extent([]), null);
  assert.equal(extent([null, NaN, undefined]), null);
});

test("definedRange frames to the first..last real value (the TDEE-chart fix)", () => {
  // Leading gaps (pre-window warm-up) must NOT stretch the axis.
  assert.deepEqual(definedRange([null, null, 2400, 2450, null, 2500, null]), [2, 5]);
});

test("[adversarial] definedRange of all-missing is null", () => {
  assert.equal(definedRange([null, null, NaN]), null);
});

test("padDomain expands by a fraction of the span", () => {
  assert.deepEqual(padDomain([10, 20], 0.1), [9, 21]);
});

test("[adversarial] padDomain on a zero-span domain uses the absolute minPad", () => {
  assert.deepEqual(padDomain([5, 5], 0.1, 0.5), [4.5, 5.5]);
});

test("ticks land on round 1/2/5 steps inside the domain", () => {
  assert.deepEqual(ticks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
});

test("[adversarial] ticks on a zero span / bad count return []", () => {
  assert.deepEqual(ticks(5, 5, 3), []);
  assert.deepEqual(ticks(0, 10, 0), []);
});

test("xLabelIndices dedupes to first/last for tiny series", () => {
  assert.deepEqual(xLabelIndices(0, 1), []);
  assert.deepEqual(xLabelIndices(1, 1), [0]);
  assert.deepEqual(xLabelIndices(2, 1), [0, 1]);
  assert.deepEqual(xLabelIndices(5, 1), [0, 2, 4]);
});

test("weekStartIndices marks Mondays (UTC)", () => {
  // 2026-01-05 is a Monday; 2026-01-12 the next Monday.
  const dates = ["2026-01-04", "2026-01-05", "2026-01-06", "2026-01-11", "2026-01-12"];
  assert.deepEqual(weekStartIndices(dates), [1, 4]);
});

test("linePath breaks the polyline across null gaps", () => {
  const d = linePath([{ x: 0, y: 0 }, null, { x: 10, y: 10 }, { x: 20, y: 5 }]);
  // A gap forces a new "M" (move) rather than an "L" (line) — the gap is not bridged.
  assert.equal(d, "M0,0M10,10L20,5");
});

test("round1 rounds to one decimal", () => {
  assert.equal(round1(1.24), 1.2);
  assert.equal(round1(1.25), 1.3);
});

// ---------- 3. goal-line logic ---------- //

test("daysBetween counts whole UTC days, signed", () => {
  assert.equal(daysBetween("2026-01-01", "2026-01-08"), 7);
  assert.equal(daysBetween("2026-01-08", "2026-01-01"), -7);
  assert.equal(daysBetween("2026-01-01", "2026-01-01"), 0);
});

test("goalLineAnchors: start = trend at first logged day, end = target on target date", () => {
  // Display window starts at the first logged day (All range).
  const a = goalLineAnchors({
    displayStartIso: "2026-04-13",
    firstLoggedIso: "2026-04-13",
    startWeight: 84.6,
    targetIso: "2026-09-01",
    targetWeight: 78,
  });
  assert.equal(a.startX, 0); // first logged day sits at index 0
  assert.equal(a.startWeight, 84.6);
  assert.equal(a.endX, daysBetween("2026-04-13", "2026-09-01"));
  assert.equal(a.endWeight, 78);
});

test("goalLineAnchors: start anchor is negative when the first logged day precedes the window", () => {
  // A 30-day window: first logged day is earlier, so the plan enters from the left.
  const a = goalLineAnchors({
    displayStartIso: "2026-06-05",
    firstLoggedIso: "2026-04-13",
    startWeight: 84.6,
    targetIso: "2026-09-01",
    targetWeight: 78,
  });
  assert.ok(a.startX < 0);
  assert.equal(a.startX, daysBetween("2026-06-05", "2026-04-13"));
});

test("[adversarial / honest] goalLineAnchors returns null when no target is set", () => {
  const base = {
    displayStartIso: "2026-04-13",
    firstLoggedIso: "2026-04-13",
    startWeight: 84.6,
  };
  assert.equal(goalLineAnchors({ ...base, targetIso: null, targetWeight: null }), null);
  assert.equal(goalLineAnchors({ ...base, targetIso: "2026-09-01", targetWeight: null }), null);
  assert.equal(goalLineAnchors({ ...base, targetIso: null, targetWeight: 78 }), null);
  // No start anchor (no logged trend yet) → also null.
  assert.equal(
    goalLineAnchors({
      displayStartIso: "2026-04-13",
      firstLoggedIso: null,
      startWeight: null,
      targetIso: "2026-09-01",
      targetWeight: 78,
    }),
    null,
  );
});

test("goalWeightAt interpolates along the plan and extrapolates past it", () => {
  const a = { startX: 0, startWeight: 84, endX: 10, endWeight: 74 }; // −1 kg/day
  assert.ok(near(goalWeightAt(a, 0), 84));
  assert.ok(near(goalWeightAt(a, 10), 74));
  assert.ok(near(goalWeightAt(a, 5), 79));
  assert.ok(near(goalWeightAt(a, 15), 69)); // extrapolation beyond the target
});

test("[adversarial] goalWeightAt on a same-day plan returns the start weight (no NaN)", () => {
  const a = { startX: 4, startWeight: 80, endX: 4, endWeight: 78 };
  assert.equal(goalWeightAt(a, 4), 80);
  assert.equal(goalWeightAt(a, 99), 80);
});

test("indexDomain frames the data, extending to a FUTURE target when the goal shows", () => {
  assert.deepEqual(indexDomain(30), [0, 29]); // no goal → just the data
  // Target 20 days past the last point → domain stretches to include it.
  assert.deepEqual(indexDomain(30, { startX: 0, endX: 49 }), [0, 49]);
});

test("indexDomain does NOT shrink for a past / in-range target (honest past-target case)", () => {
  // Target already reached (endX within the data) must not pull the right edge in.
  assert.deepEqual(indexDomain(30, { startX: 0, endX: 10 }), [0, 29]);
});

test("[adversarial] indexDomain stays sane for 0 / 1 point", () => {
  assert.deepEqual(indexDomain(0), [0, 1]);
  assert.deepEqual(indexDomain(1), [0, 1]);
});
