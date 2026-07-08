import { test } from "node:test";
import assert from "node:assert/strict";

import {
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
