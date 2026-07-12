import { test } from "node:test";
import assert from "node:assert/strict";

import {
  allCases,
  diagonal,
  domainFor,
  fmtPct,
  scatterPoints,
} from "../validation-view.js";

const report = {
  scenarios: [
    {
      id: "headline",
      cases: [
        { true_tdee: 2200, recovered_tdee: 2230, within_tolerance: true },
        { true_tdee: 2800, recovered_tdee: 2500, within_tolerance: false },
        { true_tdee: 2500, recovered_tdee: null, within_tolerance: false }, // declined
      ],
    },
    {
      id: "robust",
      cases: [{ true_tdee: 2500, recovered_tdee: 2520, within_tolerance: true }],
    },
  ],
};

test("fmtPct rounds to one decimal percent", () => {
  assert.equal(fmtPct(0.933), "93.3%");
  assert.equal(fmtPct(1), "100%");
  assert.equal(fmtPct(0.8), "80%");
});

test("allCases flattens and tags with scenario id", () => {
  const cases = allCases(report);
  assert.equal(cases.length, 4);
  assert.equal(cases[0].scenario, "headline");
  assert.equal(cases[3].scenario, "robust");
});

test("domainFor spans both true and recovered values with padding", () => {
  const d = domainFor(allCases(report));
  assert.ok(d.min < 2200); // padded below the smallest value
  assert.ok(d.max > 2800); // padded above the largest
});

test("scatterPoints skips declined (null) recoveries and flags pass/fail", () => {
  const geom = { width: 640, height: 360, pad: 44, domain: { min: 2100, max: 2900 } };
  const pts = scatterPoints(allCases(report), geom);
  assert.equal(pts.length, 3); // the null-recovery case is dropped
  assert.equal(pts.filter((p) => p.within).length, 2);
  // Points stay within the drawable area.
  for (const p of pts) {
    assert.ok(p.cx >= geom.pad && p.cx <= geom.width - geom.pad);
    assert.ok(p.cy >= geom.pad && p.cy <= geom.height - geom.pad);
  }
});

test("scatterPoints maps a perfect recovery onto the y=x diagonal", () => {
  const geom = { width: 640, height: 360, pad: 44, domain: { min: 2000, max: 3000 } };
  const perfect = scatterPoints([{ true_tdee: 2500, recovered_tdee: 2500, within_tolerance: true }], geom);
  const d = diagonal(geom);
  // The midpoint of the diagonal equals a perfect (2500,2500) point.
  assert.ok(Math.abs(perfect[0].cx - (d.x1 + d.x2) / 2) < 0.5);
  assert.ok(Math.abs(perfect[0].cy - (d.y1 + d.y2) / 2) < 0.5);
});

test("diagonal runs bottom-left to top-right (recovered increases with true)", () => {
  const geom = { width: 640, height: 360, pad: 44, domain: { min: 2000, max: 3000 } };
  const d = diagonal(geom);
  assert.ok(d.x2 > d.x1); // rightward
  assert.ok(d.y2 < d.y1); // upward (SVG y grows downward)
});
