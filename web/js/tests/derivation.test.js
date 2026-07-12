import { test } from "node:test";
import assert from "node:assert/strict";

import { caveatsForState, formatDerivation } from "../derivation.js";

// A realistic 'ok' TdeeResult whose derivation reconciles with the headline.
const okTdee = {
  status: "ok",
  tdee: 2290.8578,
  ci_low: 2270.324,
  ci_high: 2311.3917,
  n_valid_intake_days: 14,
  low_confidence_reason: null,
  derivation: {
    mean_intake_kcal: 2100.0,
    slope_kg_per_day: -0.024792,
    slope_se: 0.001361,
    energy_density: 7700.0,
    alpha: 0.1,
    n_regression_points: 14,
  },
};

test("formatDerivation returns null when there is no data-derived estimate", () => {
  assert.equal(formatDerivation({ status: "insufficient_data", tdee: null, derivation: null }), null);
  assert.equal(formatDerivation(null), null);
});

test("formatDerivation's visible chain lands on the visible TDEE with '='", () => {
  const f = formatDerivation(okTdee);
  assert.equal(f.tdeeDisp, 2291);
  assert.equal(f.exact, true);
  assert.equal(f.connector, "=");
  // The visible arithmetic actually reproduces the visible TDEE (honesty guardrail):
  const visible = Math.round(f.intakeDisp - f.slopeDisp * f.edDisp);
  assert.equal(visible, f.tdeeDisp);
  assert.match(f.chainText, /= 2,291 kcal\/day/);
});

test("formatDerivation marks ≈ when display rounding can't reproduce the TDEE exactly", () => {
  // Full-precision: 2000.5 − 0.00013×7700 = 1999.499 (→ shown 1999). But the intake
  // rounds UP to 2001, and no slope precision can undo that half-kcal flip, so the
  // visible arithmetic lands on 2000, not 1999 → must be shown as ≈, never a false '='.
  const t = {
    status: "ok",
    tdee: 1999.499,
    ci_low: 1980,
    ci_high: 2019,
    n_valid_intake_days: 12,
    derivation: {
      mean_intake_kcal: 2000.5,
      slope_kg_per_day: 0.00013,
      slope_se: 0.002,
      energy_density: 7700.0,
      alpha: 0.1,
      n_regression_points: 12,
    },
  };
  const f = formatDerivation(t);
  assert.equal(f.exact, false);
  assert.equal(f.connector, "≈");
  assert.match(f.chainText, /≈/);
  // And the invariant holds: it never claims '=' unless the visible math truly lands.
  assert.notEqual(Math.round(f.intakeDisp - f.slopeDisp * f.edDisp), f.tdeeDisp);
});

test("formatDerivation exposes the CI and its half-width", () => {
  const f = formatDerivation(okTdee);
  assert.ok(f.ci);
  assert.equal(f.ci.halfWidth, Math.round(okTdee.ci_high - okTdee.tdee));
  assert.match(f.ci.text, /95% CI/);
});

test("caveatsForState: 'ok' still shows the optimistic-CI caveat (whenever a CI is shown)", () => {
  const kinds = caveatsForState("ok", null, 0.1).map((c) => c.kind);
  assert.deepEqual(kinds, ["optimistic_ci", "energy_density", "smoothing_lag"]);
});

test("caveatsForState: low_confidence prepends the WHY (wide interval)", () => {
  const items = caveatsForState("low_confidence", "wide_interval", 0.1);
  assert.equal(items[0].kind, "why_low_confidence");
  assert.match(items[0].text, /noisy or sparse/);
  assert.ok(items.some((c) => c.kind === "optimistic_ci"));
});

test("caveatsForState: implausible-value reason is explained", () => {
  const items = caveatsForState("low_confidence", "implausible_value", 0.1);
  assert.equal(items[0].kind, "why_low_confidence");
  assert.match(items[0].text, /plausible range/);
});

test("caveatsForState: both reasons yield two why-lines", () => {
  const whys = caveatsForState("low_confidence", "wide_interval_and_implausible", 0.1).filter(
    (c) => c.kind === "why_low_confidence",
  );
  assert.equal(whys.length, 2);
});

test("caveatsForState: below threshold has no data-derived caveats", () => {
  assert.deepEqual(caveatsForState("insufficient_data", null, 0.1), []);
});

test("caveatsForState: the smoothing caveat mentions the actual alpha", () => {
  const smoothing = caveatsForState("ok", null, 0.25).find((c) => c.kind === "smoothing_lag");
  assert.match(smoothing.text, /α=0\.25/);
});
