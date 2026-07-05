/**
 * Status → display mapping — pure helpers, no DOM.
 *
 * Extracted from app.js's `renderPlateau` / `renderHero` (no behaviour change) so the
 * honest-state rules can be unit-tested: the UI must surface exactly what the
 * estimator returned and never fabricate a TDEE number when it declined. These
 * functions decide *what* to show; the render code only writes it to the DOM.
 */

import { fromKg, unitLabel } from "./units.js";

/** Plateau state → [label, css-class]. */
export const PLATEAU_LABEL = {
  on_track: ["On track", "is-ontrack"],
  slowing: ["Slowing", "is-slowing"],
  stalled: ["Stalled", "is-stalled"],
  undetermined: ["Gathering data", "is-undetermined"],
};

/** Plateau state → {label, cls}; unknown states fall back to a neutral dash. */
export function plateauLabel(state) {
  const [label, cls] = PLATEAU_LABEL[state] ?? ["–", "is-undetermined"];
  return { label, cls };
}

/**
 * How the TDEE hero should present a given estimator result, honestly:
 *  - `insufficient_data` → NO number is shown and `tdee`/CI are `null` (never
 *    fabricated); the hero shows its "keep logging" message instead.
 *  - `low_confidence` → the number IS shown but flagged, and the wide CI becomes the
 *    prominent line (`ciProminent`).
 *  - otherwise (a confident estimate) → the number is shown, unflagged.
 *
 * @param {{status: string, tdee?: number, ci_low?: number, ci_high?: number}} tdee
 */
/**
 * Raw-vs-trend gap (kg) above which the weigh-in card shows its reassurance note.
 * Below this the card stays clean — an ordinary ±0.2 kg day shouldn't nag; 0.5 kg
 * is on the order of one day's normal scale noise, so only a genuinely notable
 * swing speaks up. The comparison is always in kg (the stored unit); only the
 * displayed gap converts to the user's display unit.
 */
export const FLUCTUATION_NOTE_THRESHOLD_KG = 0.5;

/**
 * Context note for the latest weigh-in card, or `null` when no note is warranted.
 *
 * Honest by construction: it states only what the data supports — the reading sits
 * above/below the smoothed trend and short-term swings are expected. It must NOT
 * assert a cause (water, carbs, sodium, …): the app logs a calorie total, not
 * macros, so it cannot know why a reading moved.
 *
 * Returns `null` when either value is missing (no trend yet / no weigh-in) or the
 * absolute gap does not exceed `thresholdKg`.
 *
 * @param {number|null} rawKg latest raw weigh-in, kg
 * @param {number|null} trendKg smoothed trend for the same day, kg
 * @param {"kg"|"lb"} units display unit for the gap value
 * @param {number} thresholdKg gate, in kg
 */
export function fluctuationNote(rawKg, trendKg, units, thresholdKg = FLUCTUATION_NOTE_THRESHOLD_KG) {
  if (rawKg == null || trendKg == null) return null;
  const gapKg = rawKg - trendKg;
  if (Math.abs(gapKg) <= thresholdKg) return null;
  const gap = `${Math.abs(fromKg(gapKg, units)).toFixed(1)} ${unitLabel(units)}`;
  return gapKg > 0
    ? `Today's reading is ${gap} above your trend — likely normal day-to-day ` +
        `fluctuation, not a change in fat loss. Your trend is what matters.`
    : `Today's reading is ${gap} below your trend — likely normal day-to-day ` +
        `fluctuation. Your trend is what matters.`;
}

export function tdeeHeroView(tdee) {
  const insufficient = tdee.status === "insufficient_data";
  const lowConfidence = tdee.status === "low_confidence";
  return {
    insufficient,
    lowConfidence,
    showNumber: !insufficient, // a real TDEE number is displayed
    showUnit: !insufficient,
    ciProminent: lowConfidence, // wide CI is the headline on low confidence
    tdee: insufficient ? null : tdee.tdee,
    ciLow: insufficient ? null : tdee.ci_low,
    ciHigh: insufficient ? null : tdee.ci_high,
  };
}
