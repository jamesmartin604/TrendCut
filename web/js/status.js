/**
 * Status → display mapping — pure helpers, no DOM.
 *
 * Extracted from app.js's `renderPlateau` / `renderHero` (no behaviour change) so the
 * honest-state rules can be unit-tested: the UI must surface exactly what the
 * estimator returned and never fabricate a TDEE number when it declined. These
 * functions decide *what* to show; the render code only writes it to the DOM.
 */

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
