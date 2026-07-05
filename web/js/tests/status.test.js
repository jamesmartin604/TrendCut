/**
 * Status → display mapping. The point of these tests is the HONEST-state contract:
 * the UI must surface exactly what the estimator returned and must never fabricate a
 * TDEE number when the estimator declined. Run with `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { plateauLabel, tdeeHeroView } from "../status.js";

test("plateauLabel maps every known grade to its label + class", () => {
  assert.deepEqual(plateauLabel("on_track"), { label: "On track", cls: "is-ontrack" });
  assert.deepEqual(plateauLabel("slowing"), { label: "Slowing", cls: "is-slowing" });
  assert.deepEqual(plateauLabel("stalled"), { label: "Stalled", cls: "is-stalled" });
  assert.deepEqual(plateauLabel("undetermined"), {
    label: "Gathering data",
    cls: "is-undetermined",
  });
});

test("[adversarial] plateauLabel falls back for an unknown state (no crash)", () => {
  assert.deepEqual(plateauLabel("banana"), { label: "–", cls: "is-undetermined" });
  assert.deepEqual(plateauLabel(undefined), { label: "–", cls: "is-undetermined" });
});

test("tdeeHeroView shows a confident estimate, unflagged", () => {
  const v = tdeeHeroView({ status: "ok", tdee: 2500, ci_low: 2400, ci_high: 2600 });
  assert.equal(v.insufficient, false);
  assert.equal(v.lowConfidence, false);
  assert.equal(v.showNumber, true);
  assert.equal(v.ciProminent, false);
  assert.equal(v.tdee, 2500);
  assert.equal(v.ciLow, 2400);
  assert.equal(v.ciHigh, 2600);
});

test("tdeeHeroView flags a low-confidence estimate and makes the CI prominent", () => {
  const v = tdeeHeroView({ status: "low_confidence", tdee: 2500, ci_low: 1800, ci_high: 3200 });
  assert.equal(v.lowConfidence, true);
  assert.equal(v.showNumber, true); // the number is still shown …
  assert.equal(v.ciProminent, true); // … but the wide CI becomes the headline
  assert.equal(v.tdee, 2500);
});

test("[adversarial / honest] insufficient_data fabricates NO number", () => {
  // Even if a stray tdee value is present on the payload, the honest view nulls it out
  // and shows no number.
  const v = tdeeHeroView({ status: "insufficient_data", tdee: 9999, ci_low: 1, ci_high: 2 });
  assert.equal(v.insufficient, true);
  assert.equal(v.showNumber, false);
  assert.equal(v.showUnit, false);
  assert.equal(v.tdee, null);
  assert.equal(v.ciLow, null);
  assert.equal(v.ciHigh, null);
});
