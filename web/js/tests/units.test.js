/**
 * Unit conversion (kg/lb) round-trips + formatting. Run with `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  cmToDisplay,
  displayToCm,
  formatHeight,
  formatWeight,
  fromKg,
  heightUnitLabel,
  toKg,
  unitLabel,
} from "../units.js";

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

// --- height (cm / in) --- //
test("heightUnitLabel pairs cm with metric and in with imperial", () => {
  assert.equal(heightUnitLabel("kg"), "cm");
  assert.equal(heightUnitLabel("lb"), "in");
  assert.equal(heightUnitLabel(undefined), "cm");
});

test("cm is the identity height conversion in metric", () => {
  assert.equal(cmToDisplay(180, "kg"), 180);
  assert.equal(displayToCm(180, "kg"), 180);
});

test("cmToDisplay → displayToCm round-trips exactly in imperial", () => {
  for (const cm of [0, 120.5, 165, 180, 250]) {
    assert.ok(near(displayToCm(cmToDisplay(cm, "lb"), "lb"), cm));
  }
});

test("[drift] height survives a cm→in→cm round-trip through the 1-dp display", () => {
  // The pinned concern: the on-screen inch value is rounded to 1 dp, so converting
  // that display string back to cm must not drift more than the rounding allows
  // (0.05 in ≈ 0.13 cm). This is why the settings form keeps a canonical cm and
  // only re-derives from the field when the user actually edits it.
  for (const cm of [150, 165, 172, 180, 190, 201]) {
    const shown = Number(formatHeight(cm, "lb")); // e.g. 70.9
    assert.ok(Math.abs(displayToCm(shown, "lb") - cm) <= 0.13);
  }
});

test("formatHeight rounds cm to whole numbers and inches to 1 dp; null is empty", () => {
  assert.equal(formatHeight(180, "kg"), "180");
  assert.equal(formatHeight(180.4, "kg"), "180");
  assert.equal(formatHeight(180, "lb"), "70.9"); // 180 / 2.54 = 70.866 → 70.9
  assert.equal(formatHeight(null, "kg"), "");
});
