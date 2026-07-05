/**
 * Unit conversion (kg/lb) round-trips + formatting. Run with `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { formatWeight, fromKg, toKg, unitLabel } from "../units.js";

const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

test("unitLabel is kg unless lb is explicitly chosen", () => {
  assert.equal(unitLabel("kg"), "kg");
  assert.equal(unitLabel("lb"), "lb");
  assert.equal(unitLabel(undefined), "kg");
});

test("kg is the identity conversion", () => {
  assert.equal(fromKg(80, "kg"), 80);
  assert.equal(toKg(80, "kg"), 80);
});

test("fromKg → toKg round-trips in lb", () => {
  for (const kg of [0, 47.35, 80, 123.456, 400]) {
    assert.ok(near(toKg(fromKg(kg, "lb"), "lb"), kg));
  }
});

test("known lb conversion (100 kg ≈ 220.462 lb)", () => {
  assert.ok(near(fromKg(100, "lb"), 220.46226218, 1e-6));
});

test("formatWeight fixes decimals in the chosen unit", () => {
  assert.equal(formatWeight(80, "kg"), "80.0");
  assert.equal(formatWeight(80, "lb"), "176.4"); // 80 kg → 176.37 lb → 1dp
  assert.equal(formatWeight(80, "kg", 2), "80.00");
});

test("[adversarial] null weight formats as an en-dash and never NaN", () => {
  assert.equal(formatWeight(null, "kg"), "–");
  assert.equal(fromKg(null, "lb"), null);
  assert.equal(toKg(null, "lb"), null);
});
