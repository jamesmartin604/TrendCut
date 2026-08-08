/**
 * Status → display mapping. The point of these tests is the HONEST-state contract:
 * the UI must surface exactly what the estimator returned and must never fabricate a
 * TDEE number when the estimator declined. Run with `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FLUCTUATION_NOTE_THRESHOLD_KG,
  INSTABILITY_RANGE_KCAL,
  LARGE_SIGMA,
  MAD_TO_SD,
  MIN_RESIDUALS_FOR_DISPERSION,
  MIN_STABILITY_POINTS,
  NOTABLE_SIGMA,
  STABILITY_LOOKBACK_DAYS,
  deviationTier,
  estimateStability,
  fluctuationNote,
  plateauLabel,
  robustDispersion,
  tdeeHeroView,
} from "../status.js";

/**
 * `n` residuals whose robust dispersion is exactly `sd`.
 *
 * Half sit at −d and half at +d around zero, so the median is 0 and every absolute
 * deviation is d — making the MAD exactly d and the dispersion exactly d × MAD_TO_SD.
 * Tests can then place a residual at a precise multiple of σ without depending on
 * any particular kg value.
 */
function residualsWithDispersion(sd, n = MIN_RESIDUALS_FOR_DISPERSION) {
  const d = sd / MAD_TO_SD;
  return Array.from({ length: n }, (_, i) => (i % 2 === 0 ? -d : d));
}

/**
 * The note as the reader sees it: lead + rest joined exactly as the DOM joins them.
 * The copy is split so only the lead can be emphasised, but assertions about wording
 * and honesty apply to the whole sentence, not to whichever half it landed in.
 */
function fullText(note) {
  return `${note.lead} ${note.rest}`;
}

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

test("fluctuationNote is null when raw or trend is missing", () => {
  assert.equal(fluctuationNote(null, 80.0, "kg"), null);
  assert.equal(fluctuationNote(80.8, null, "kg"), null);
  assert.equal(fluctuationNote(null, null, "kg"), null);
});

test("fluctuationNote is null at or below the threshold (ordinary days stay clean)", () => {
  assert.equal(fluctuationNote(80.2, 80.0, "kg"), null);
  // Boundary: a gap exactly equal to the threshold does NOT trigger the note.
  assert.equal(fluctuationNote(80.0 + FLUCTUATION_NOTE_THRESHOLD_KG, 80.0, "kg"), null);
  assert.equal(fluctuationNote(80.0 - FLUCTUATION_NOTE_THRESHOLD_KG, 80.0, "kg"), null);
});

test("fluctuationNote above trend: direction + gap in display units, no cause", () => {
  const note = fluctuationNote(80.8, 80.0, "kg");
  assert.equal(note.tier, "normal");
  assert.match(fullText(note), /0\.8 kg above your trend/);
  assert.match(fullText(note), /not a change in fat loss/);
  assert.match(fullText(note), /trend is what matters/i);
});

test("fluctuationNote below trend: direction-aware wording", () => {
  const note = fluctuationNote(79.2, 80.0, "kg");
  assert.match(fullText(note), /0\.8 kg below your trend/);
  assert.match(fullText(note), /likely normal day-to-day fluctuation/);
});

test("fluctuationNote respects lb display units (gap converted, gate stays in kg)", () => {
  // 0.8 kg gap ≈ 1.8 lb; the threshold comparison happened in kg.
  const note = fluctuationNote(80.8, 80.0, "lb");
  assert.match(fullText(note), /1\.8 lb above your trend/);
});

test("[honest] the normal-tier note never asserts a cause for the swing", () => {
  for (const note of [fluctuationNote(81.0, 80.0, "kg"), fluctuationNote(79.0, 80.0, "kg")]) {
    assert.equal(note.tier, "normal");
    for (const causal of ["water", "carb", "sodium", "because", "glycogen", "ate"]) {
      assert.ok(
        !fullText(note).toLowerCase().includes(causal),
        `note must not claim "${causal}"`,
      );
    }
  }
});

// ---------- robust dispersion ---------- //

test("robustDispersion is the MAD scaled to SD-comparable units", () => {
  // Median 0; absolute deviations all 1 → MAD 1 → dispersion 1 × MAD_TO_SD.
  const residuals = Array.from({ length: 12 }, (_, i) => (i % 2 === 0 ? -1 : 1));
  assert.ok(Math.abs(robustDispersion(residuals) - MAD_TO_SD) < 1e-12);
});

test("robustDispersion is robust: one huge whoosh barely moves the bar", () => {
  const ordinary = residualsWithDispersion(0.4, 20);
  const withWhoosh = [...ordinary.slice(0, 19), -8.0]; // an extreme outlier
  const before = robustDispersion(ordinary);
  const after = robustDispersion(withWhoosh);
  // An SD would roughly quadruple here; the MAD-based bar stays within 20%.
  assert.ok(Math.abs(after - before) / before < 0.2, `bar moved from ${before} to ${after}`);
});

test("[sparse] robustDispersion declines below MIN_RESIDUALS_FOR_DISPERSION", () => {
  const tooFew = residualsWithDispersion(0.4, MIN_RESIDUALS_FOR_DISPERSION - 1);
  assert.equal(robustDispersion(tooFew), null);
  assert.equal(robustDispersion([]), null);
  assert.equal(robustDispersion(undefined), null);
  // Exactly at the minimum it DOES characterise the noise (boundary is inclusive).
  assert.ok(robustDispersion(residualsWithDispersion(0.4)) > 0);
});

test("[degenerate] robustDispersion declines on zero spread", () => {
  // Every residual identical → MAD 0. Returning 0 would make every ratio infinite.
  assert.equal(robustDispersion(new Array(20).fill(0)), null);
  assert.equal(robustDispersion(new Array(20).fill(0.3)), null);
});

test("robustDispersion ignores non-finite entries when counting", () => {
  const rs = [...residualsWithDispersion(0.4), NaN, null, undefined, Infinity];
  assert.ok(Math.abs(robustDispersion(rs) - 0.4) < 1e-12);
  // …and those entries do not pad a sparse sample up to the minimum.
  assert.equal(robustDispersion([0.1, -0.1, NaN, null, Infinity, NaN, null]), null);
});

// ---------- tier classification ---------- //

test("deviationTier classifies normal / notable / large by sigma multiples", () => {
  const sd = 0.4;
  const rs = residualsWithDispersion(sd);
  assert.equal(deviationTier(sd * 1.5, rs), "normal");
  assert.equal(deviationTier(sd * 2.5, rs), "notable");
  assert.equal(deviationTier(sd * 4.0, rs), "large");
});

test("deviationTier boundaries sit on the named constants, inclusive", () => {
  const rs = residualsWithDispersion(0.4);
  // Scale from the dispersion the function itself reports, not the nominal value the
  // helper aimed at: the MAD round-trip is not bit-exact, and this test is about the
  // comparison, not about floating point.
  const sd = robustDispersion(rs);
  const nudge = 1 - 1e-9;
  assert.equal(deviationTier(sd * NOTABLE_SIGMA * nudge, rs), "normal");
  assert.equal(deviationTier(sd * NOTABLE_SIGMA, rs), "notable"); // boundary is inclusive
  assert.equal(deviationTier(sd * LARGE_SIGMA * nudge, rs), "notable");
  assert.equal(deviationTier(sd * LARGE_SIGMA, rs), "large"); // boundary is inclusive
});

test("deviationTier is symmetric: a big drop is as notable as a big gain", () => {
  const sd = 0.4;
  const rs = residualsWithDispersion(sd);
  for (const mult of [1.5, 2.5, 4.0]) {
    assert.equal(
      deviationTier(sd * mult, rs),
      deviationTier(-sd * mult, rs),
      `sign changed the tier at ${mult}σ`,
    );
  }
  assert.equal(deviationTier(-sd * 4.0, rs), "large"); // a whoosh reaches "large"
});

test("[sparse] deviationTier falls back to normal on thin data — no crying wolf", () => {
  // A residual that WOULD be large if we could characterise the noise…
  const thin = residualsWithDispersion(0.4, MIN_RESIDUALS_FOR_DISPERSION - 1);
  assert.equal(deviationTier(0.4 * 5, thin), "normal");
  assert.equal(deviationTier(0.4 * 5, []), "normal");
});

test("[degenerate] deviationTier treats zero dispersion as normal", () => {
  assert.equal(deviationTier(2.0, new Array(20).fill(0)), "normal");
  assert.equal(deviationTier(2.0, new Array(20).fill(0.3)), "normal");
});

test("[adversarial] deviationTier survives a missing residual", () => {
  const rs = residualsWithDispersion(0.4);
  assert.equal(deviationTier(null, rs), "normal");
  assert.equal(deviationTier(NaN, rs), "normal");
});

test("today's own residual does not mask itself in the dispersion sample", () => {
  // The residual list INCLUDES today's, so a big day nudges its own bar upward.
  // With a MAD over >= 10 points that nudge must not demote the tier.
  const ordinary = residualsWithDispersion(0.3, 20);
  const todayKg = 2.5;
  assert.equal(deviationTier(todayKg, [...ordinary, todayKg]), "large");
});

// ---------- tier → copy ---------- //

test("fluctuationNote escalates wording with the tier, both signs", () => {
  const sd = 0.3;
  const rs = residualsWithDispersion(sd);
  const at = (mult) => fluctuationNote(80.0 + sd * mult, 80.0, "kg", rs);

  assert.equal(at(2.5).tier, "notable");
  assert.match(fullText(at(2.5)), /well above your recent day-to-day range/);
  assert.match(fullText(at(2.5)), /trend is what matters/i);

  assert.equal(at(9).tier, "large");
  assert.match(fullText(at(9)), /unusually big single-day move/);
  assert.match(fullText(at(9)), /the trend is the signal/);

  // Below-trend readings reach the same tiers with mirrored wording.
  assert.equal(at(-2.5).tier, "notable");
  assert.match(fullText(at(-2.5)), /well below your recent day-to-day range/);
  assert.equal(at(-9).tier, "large");
  assert.match(fullText(at(-9)), /unusually big single-day move/);
});

test("the lead carries the fact and the rest carries the reassurance", () => {
  // Dispersion 0.4 so that even the normal-tier case (1.5σ = 0.6 kg) still clears the
  // 0.5 kg floor gate and produces a note to inspect.
  const sd = 0.4;
  const rs = residualsWithDispersion(sd);
  const at = (mult) => fluctuationNote(80.0 + sd * mult, 80.0, "kg", rs);
  assert.equal(at(1.5).tier, "normal"); // guard: the loop below must cover all 3 tiers

  for (const note of [at(1.5), at(2.5), at(9)]) {
    // Every tier splits, so the DOM shape never changes — only the styling does.
    assert.ok(note.lead.length > 0 && note.rest.length > 0);
    // The lead is a complete sentence: the two halves join with one space and read
    // correctly even where nothing is emphasised.
    assert.match(note.lead, /\.$/);
    assert.match(note.rest, /^[A-Z]/);
    // The measured fact is in the emphasised half…
    assert.match(note.lead, /above your trend/);
    // …and the trend reassurance is never left stranded in the bold part alone.
    assert.match(note.rest, /trend/i);
  }

  // Only the emphasised tiers move the cause explanation out of the lead.
  assert.match(at(9).rest, /water rather than fat/);
  assert.ok(!/water/.test(at(9).lead), "the large tier's lead states the move, not the cause");
});

test("[honest] emphasised tiers describe physiology, never THIS reading's cause", () => {
  const sd = 0.3;
  const rs = residualsWithDispersion(sd);
  const notes = [
    fluctuationNote(80.0 + sd * 2.5, 80.0, "kg", rs),
    fluctuationNote(80.0 - sd * 2.5, 80.0, "kg", rs),
    fluctuationNote(80.0 + sd * 9, 80.0, "kg", rs),
    fluctuationNote(80.0 - sd * 9, 80.0, "kg", rs),
  ];
  for (const note of notes) {
    // Never an assertion about what the user did — the app logs calories, not macros.
    for (const claim of ["you ate", "you had", "because you", "your sodium", "your carbs"]) {
      assert.ok(
        !fullText(note).toLowerCase().includes(claim),
        `note must not claim "${claim}": ${fullText(note)}`,
      );
    }
    // Always reframes toward the trend rather than a stall.
    assert.match(fullText(note), /trend/i);
    assert.ok(!/stall|plateau|gained fat|losing fat has stopped/i.test(fullText(note)));
  }
});

test("[anti-drift] no tier copy hardcodes a kg value beyond the formatted gap", () => {
  const sd = 0.3;
  const rs = residualsWithDispersion(sd);
  for (const mult of [2.5, 9]) {
    const text = fullText(fluctuationNote(80.0 + sd * mult, 80.0, "kg", rs));
    // The only number in the copy is the computed gap itself.
    const numbers = text.match(/\d+(\.\d+)?/g) ?? [];
    assert.deepEqual(numbers, [(sd * mult).toFixed(1)], `unexpected literals in: ${text}`);
  }
});

test("the kg floor gate still suppresses a sub-threshold gap, however unusual", () => {
  // A very consistent weigher: tiny dispersion, so 0.4 kg is many sigma — but it is
  // still below the absolute floor, so no note appears at all. The two guards do
  // different jobs; the sigma tier only escalates a note that is already showing.
  const rs = residualsWithDispersion(0.05);
  assert.equal(deviationTier(0.4, rs), "large");
  assert.equal(fluctuationNote(80.4, 80.0, "kg", rs), null);
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

// ---------- estimate stability gate ---------- //

/** `/tdee-history`-shaped points from a list of TDEE values, with a given CI half-width. */
function historyPoints(values, halfWidth = 30) {
  return values.map((tdee, i) => ({
    date: `2026-07-${String(21 + i).padStart(2, "0")}`,
    status: "ok",
    tdee,
    ci_low: tdee == null ? null : tdee - halfWidth,
    ci_high: tdee == null ? null : tdee + halfWidth,
  }));
}

/** The real Jul 21-31 replay (window_days=28) that motivated this gate: range 117. */
const JUL_21_31 = [2371, 2376, 2386, 2412, 2429, 2432, 2434, 2444, 2457, 2472, 2493];

test("estimateStability: a quiet series is stable", () => {
  // The real Jul 22 lookback — the estimate barely moved.
  const s = estimateStability(historyPoints([2383, 2380, 2379, 2376, 2378, 2381, 2377]));
  assert.equal(s.stable, true);
  assert.equal(s.reason, "settled");
  assert.ok(s.range < INSTABILITY_RANGE_KCAL);
});

test("estimateStability: the real Jul 21-31 climb gates", () => {
  const s = estimateStability(historyPoints(JUL_21_31));
  assert.equal(s.stable, false);
  assert.equal(s.reason, "moving");
  // The lookback is the last 10 of the 11 days, so Jul 21's 2371 falls outside it —
  // giving the 117 kcal range the diagnostic measured for the as-of-Jul-31 window.
  assert.equal(s.lo, 2376);
  assert.equal(s.hi, 2493);
  assert.equal(s.range, 117);
  assert.equal(s.n, 10);
});

test("estimateStability: only the last STABILITY_LOOKBACK_DAYS count", () => {
  // A huge old swing followed by a settled recent stretch must NOT gate.
  const s = estimateStability(historyPoints([1800, 2900, 1900, ...new Array(10).fill(2400)]));
  assert.equal(s.n, STABILITY_LOOKBACK_DAYS);
  assert.equal(s.stable, true);
});

test("[sparse] estimateStability does not judge thin history", () => {
  // Deliberately WILD values (range 200, twice the threshold) with too few points:
  // the only thing preventing a gate here is the sparse guard, so this proves the
  // guard rather than passing because the series happened to be quiet.
  const wild = [2300, 2500, 2350, 2480, 2400];
  assert.equal(wild.length, MIN_STABILITY_POINTS - 1);
  assert.ok(Math.max(...wild) - Math.min(...wild) > INSTABILITY_RANGE_KCAL);

  const thin = estimateStability(historyPoints(wild));
  assert.equal(thin.stable, true, "must not gate when it cannot judge");
  assert.equal(thin.reason, "insufficient_history");
  assert.equal(thin.range, null);
  assert.equal(thin.ratioToCi, null);

  // One more point and the same wildness DOES gate — the guard is the only difference.
  const atMin = estimateStability(historyPoints([...wild, 2400]));
  assert.equal(atMin.n, MIN_STABILITY_POINTS);
  assert.equal(atMin.stable, false);
  assert.equal(atMin.reason, "moving");
});

test("[sparse] insufficient_data days carry no estimate and don't count", () => {
  const pts = historyPoints([null, null, null, null, 2400, 2410, 2405]);
  const s = estimateStability(pts);
  assert.equal(s.n, 3);
  assert.equal(s.stable, true);
  assert.equal(s.reason, "insufficient_history");
  assert.equal(estimateStability(historyPoints([])).stable, true);
  assert.equal(estimateStability(null).stable, true);
});

test("estimateStability boundary sits on INSTABILITY_RANGE_KCAL, inclusive", () => {
  const base = 2400;
  const exact = historyPoints([...new Array(9).fill(base), base + INSTABILITY_RANGE_KCAL]);
  assert.equal(estimateStability(exact).stable, true, "exactly at the threshold is stable");
  const over = historyPoints([...new Array(9).fill(base), base + INSTABILITY_RANGE_KCAL + 1]);
  assert.equal(estimateStability(over).stable, false);
});

test("[anti-drift] ratioToCi is reported but never gates", () => {
  // A tiny reported CI makes the ratio enormous, but the movement is sub-threshold —
  // this MUST stay stable. Gating on the ratio is what the coverage test disproved:
  // the CI is too narrow essentially always, so a ratio gate would be permanently on
  // and would bite the cleanest loggers hardest.
  const s = estimateStability(historyPoints([2400, 2410, 2420, 2430, 2440, 2450, 2445], 1));
  assert.ok(s.ratioToCi > 20, `ratio should be large, got ${s.ratioToCi}`);
  assert.equal(s.stable, true);
  assert.ok(s.range <= INSTABILITY_RANGE_KCAL);
});

test("estimateStability is sign-agnostic: a falling estimate gates too", () => {
  const s = estimateStability(historyPoints([...JUL_21_31].reverse()));
  assert.equal(s.stable, false);
  assert.equal(s.range, 101); // last 10 of the reversed series: 2472 down to 2371
});

test("tdeeHeroView: unstable routes to the same prominent line as low confidence", () => {
  const unstable = { stable: false, lo: 2371, hi: 2493, range: 122, n: 10, ratioToCi: 4 };
  const v = tdeeHeroView({ status: "ok", tdee: 2493, ci_low: 2459, ci_high: 2527 }, unstable);
  assert.equal(v.unstable, true);
  assert.equal(v.ciProminent, true); // same treatment, not a parallel one
  assert.equal(v.rangeLo, 2371);
  assert.equal(v.rangeHi, 2493);
  assert.equal(v.rangeDays, 10);
  assert.equal(v.showNumber, true); // the point estimate is still shown
});

test("tdeeHeroView: the gate defers to the estimator's own low_confidence", () => {
  // Already flagged → presentation is left exactly as it was, no doubled warning.
  const unstable = { stable: false, lo: 2371, hi: 2493, range: 122, n: 10, ratioToCi: 4 };
  const v = tdeeHeroView(
    { status: "low_confidence", tdee: 2493, ci_low: 1900, ci_high: 3100 },
    unstable,
  );
  assert.equal(v.lowConfidence, true);
  assert.equal(v.unstable, false);
  assert.equal(v.rangeLo, null);
});

test("tdeeHeroView without stability is unchanged (back-compatible)", () => {
  const v = tdeeHeroView({ status: "ok", tdee: 2400, ci_low: 2360, ci_high: 2440 });
  assert.equal(v.unstable, false);
  assert.equal(v.ciProminent, false);
  assert.equal(v.rangeLo, null);
});

test("[sparse] a thin history never softens the hero", () => {
  // Same wildly-swinging-but-thin history as above: the hero must stay confident.
  const s = estimateStability(historyPoints([2300, 2500, 2350]));
  const v = tdeeHeroView({ status: "ok", tdee: 2400, ci_low: 2360, ci_high: 2440 }, s);
  assert.equal(v.unstable, false);
  assert.equal(v.ciProminent, false);
  assert.equal(v.rangeLo, null);
});
