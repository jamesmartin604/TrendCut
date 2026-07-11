import { test } from "node:test";
import assert from "node:assert/strict";

import {
  coldStartProvisionalView,
  demographicsComplete,
  hasProvisional,
  importResultMessage,
  onboardingProgress,
  onboardingProgressText,
  onboardingState,
  showOnboarding,
} from "../onboarding.js";

// Minimal /estimate shapes for the state decision.
const empty = { n_entries: 0, tdee: { status: "insufficient_data" } };
const building = {
  n_entries: 5,
  tdee: { status: "insufficient_data", n_valid_weight_days: 4, n_valid_intake_days: 6, window_days: 28 },
};
const ready = { n_entries: 40, tdee: { status: "ok" } };
const readyLowConf = { n_entries: 40, tdee: { status: "low_confidence" } };

test("onboardingState: empty when there are no entries", () => {
  assert.equal(onboardingState(empty), "empty");
  assert.equal(onboardingState(null), "empty");
  assert.equal(onboardingState(undefined), "empty");
});

test("onboardingState: building when data exists but estimator says insufficient", () => {
  assert.equal(onboardingState(building), "building");
});

test("onboardingState: ready for ok and low_confidence (a number exists)", () => {
  assert.equal(onboardingState(ready), "ready");
  assert.equal(onboardingState(readyLowConf), "ready");
});

test("showOnboarding is true unless ready", () => {
  assert.equal(showOnboarding(empty), true);
  assert.equal(showOnboarding(building), true);
  assert.equal(showOnboarding(ready), false);
});

test("onboardingProgress computes have/need against the requirements", () => {
  const p = onboardingProgress(building, { min_weight_days: 10, min_intake_days: 10 });
  assert.deepEqual(p, {
    windowDays: 28,
    weightHave: 4,
    weightTarget: 10,
    weightNeed: 6,
    intakeHave: 6,
    intakeTarget: 10,
    intakeNeed: 4,
  });
});

test("onboardingProgress never reports a negative need (clamped at 0)", () => {
  const over = {
    tdee: { n_valid_weight_days: 12, n_valid_intake_days: 15, window_days: 28 },
  };
  const p = onboardingProgress(over, { min_weight_days: 10, min_intake_days: 10 });
  assert.equal(p.weightNeed, 0);
  assert.equal(p.intakeNeed, 0);
});

test("onboardingProgressText states have-of-target for both series", () => {
  const text = onboardingProgressText({
    windowDays: 28,
    weightHave: 4,
    weightTarget: 10,
    intakeHave: 6,
    intakeTarget: 10,
  });
  assert.match(text, /last 28 days/);
  assert.match(text, /4 of 10 weigh-in days/);
  assert.match(text, /6 of 10 intake days/);
});

// --- provisional (day-1) helpers --- //
const fullDemographics = { age: 30, sex: "male", height_cm: 180, activity_level: "moderate" };

test("demographicsComplete requires all four fields", () => {
  assert.equal(demographicsComplete(fullDemographics), true);
  assert.equal(demographicsComplete({ ...fullDemographics, height_cm: null }), false);
  assert.equal(demographicsComplete({ age: 30, sex: "male" }), false);
  assert.equal(demographicsComplete(null), false);
});

test("hasProvisional reflects presence of the estimate.provisional field", () => {
  assert.equal(hasProvisional({ provisional: { tdee: 2500 } }), true);
  assert.equal(hasProvisional({ provisional: null }), false);
  assert.equal(hasProvisional({}), false);
  assert.equal(hasProvisional(null), false);
});

test("coldStartProvisionalView: shows the provisional when present", () => {
  const est = { provisional: { tdee: 2759, low: 2069, high: 3449, basis: "generic…" } };
  const view = coldStartProvisionalView(est, fullDemographics);
  assert.equal(view.kind, "provisional");
  assert.equal(view.tdee, 2759);
  assert.equal(view.low, 2069);
  assert.equal(view.high, 3449);
});

test("coldStartProvisionalView: prompts for demographics when they're missing", () => {
  const view = coldStartProvisionalView({ provisional: null }, { age: 30 });
  assert.equal(view.kind, "add_demographics");
});

test("coldStartProvisionalView: prompts for a weigh-in when demographics complete but no number", () => {
  // Demographics are complete, yet the server returned no provisional → must be the
  // missing weight anchor.
  const view = coldStartProvisionalView({ provisional: null }, fullDemographics);
  assert.equal(view.kind, "needs_weight");
});

test("importResultMessage: nothing imported is not ok", () => {
  const m = importResultMessage({ imported: 0, warnings: ["2026-01-01: skipped"] });
  assert.equal(m.ok, false);
  assert.match(m.summary, /Nothing was imported/);
  assert.deepEqual(m.warnings, ["2026-01-01: skipped"]);
});

test("importResultMessage: pluralization and warning passthrough", () => {
  assert.match(importResultMessage({ imported: 1, warnings: [] }).summary, /Imported 1 entry\./);
  const many = importResultMessage({ imported: 12, warnings: ["w1", "w2"] });
  assert.equal(many.ok, true);
  assert.match(many.summary, /Imported 12 entries\./);
  assert.deepEqual(many.warnings, ["w1", "w2"]);
});
