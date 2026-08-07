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
 *
 * This is the *floor* gate: it decides whether any note appears at all. Whether an
 * appearing note is EMPHASISED is a separate, relative question — see
 * `deviationTier`. Both guards are needed and they do different jobs: without the
 * floor, a very consistent weigher (dispersion ~0.15 kg) would be told a trivial
 * 0.4 kg move is "well above your recent range", which is crying wolf by a
 * different mechanism.
 */
export const FLUCTUATION_NOTE_THRESHOLD_KG = 0.5;

/**
 * Scale factor turning a median absolute deviation into something comparable to a
 * standard deviation (for normally distributed data). MAD is used instead of SD
 * because it is robust: an occasional whoosh or a salty-meal spike barely moves it,
 * so the "unusual" bar is set by the user's ORDINARY noise, not by their outliers.
 */
export const MAD_TO_SD = 1.4826;

/** |residual| / dispersion at or above which today's reading counts as notable. */
export const NOTABLE_SIGMA = 2;

/** |residual| / dispersion at or above which it counts as an unusually large move. */
export const LARGE_SIGMA = 3;

/**
 * Residuals needed before we will claim a reading is unusual at all. Below this the
 * MAD is too unstable to characterise anyone's noise, so we stay quiet rather than
 * assert something we cannot support — the same honesty rule the TDEE hero follows
 * when the estimator declines. Fewer points is not "no swings"; it is "we don't know".
 */
export const MIN_RESIDUALS_FOR_DISPERSION = 10;

/** Median of a list of finite numbers; `null` for an empty list. Does not mutate. */
function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  if (n === 0) return null;
  const mid = n >> 1;
  return n % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Robust, SD-comparable spread of a user's own raw-vs-trend residuals (kg), or
 * `null` when it cannot be characterised honestly.
 *
 * `null` covers both degenerate cases, so callers need only one fallback path:
 *  - too few residuals to say anything about the noise (sparse / early data), and
 *  - a zero (or non-finite) spread — e.g. every residual identical — which would
 *    otherwise make every ratio infinite and flag an ordinary day as extreme.
 *
 * @param {Array<number>} residualsKg raw − trend, kg, over the visible window
 */
export function robustDispersion(residualsKg) {
  const rs = (residualsKg ?? []).filter((r) => typeof r === "number" && Number.isFinite(r));
  if (rs.length < MIN_RESIDUALS_FOR_DISPERSION) return null;
  const centre = median(rs);
  const mad = median(rs.map((r) => Math.abs(r - centre)));
  const sd = mad * MAD_TO_SD;
  return Number.isFinite(sd) && sd > 0 ? sd : null;
}

/**
 * How unusual today's residual is *for this user*: "normal" | "notable" | "large".
 *
 * Relative by construction — the bar is a multiple of the user's own dispersion, so
 * there is no hardcoded kg anywhere in the classification. Sign-agnostic: a large
 * drop (a possible whoosh) is exactly as notable as a large gain.
 *
 * Falls back to "normal" whenever dispersion is unavailable (sparse data, zero
 * spread) or the residual itself isn't a real number: if we cannot characterise the
 * noise, we do not assert that a reading is unusual.
 *
 * @param {number|null} residualKg today's raw − trend, kg
 * @param {Array<number>} residualsKg residuals over the visible window (may include today's)
 */
export function deviationTier(residualKg, residualsKg) {
  const dispersion = robustDispersion(residualsKg);
  if (dispersion == null) return "normal";
  if (residualKg == null || !Number.isFinite(residualKg)) return "normal";
  const ratio = Math.abs(residualKg) / dispersion;
  if (ratio >= LARGE_SIGMA) return "large";
  if (ratio >= NOTABLE_SIGMA) return "notable";
  return "normal";
}

/**
 * Tier + direction → the note's wording. Kept beside the tier constants so copy and
 * thresholds stay in step; the tiers escalate EMPHASIS and specificity, never alarm.
 *
 * On cause attribution: the "normal" and "notable" tiers name no cause at all, and
 * the "large" tier states only general physiology ("swings this size are usually
 * water") — never a claim about why THIS reading moved. The app logs a calorie
 * total, not macros, so it cannot know that; saying "you ate salty food" would be
 * fabrication. Describing what a move of this size usually is, is not.
 */
function noteText(tier, gap, above) {
  const dir = above ? "above" : "below";
  if (tier === "large") {
    return (
      `Today's reading is ${gap} ${dir} your trend — an unusually big single-day ` +
      `move for you. Swings this size are almost always water rather than fat: ` +
      `sodium, carbs, and hydration can shift the scale overnight. One reading ` +
      `barely moves your trend line, and the trend is the signal.`
    );
  }
  if (tier === "notable") {
    const tail = above ? ", not a change in fat loss" : "";
    return (
      `Today's reading is ${gap} ${dir} your trend — well ${dir} your recent ` +
      `day-to-day range, but still consistent with normal water and glycogen ` +
      `swings${tail}. Your trend is what matters.`
    );
  }
  const tail = above ? ", not a change in fat loss" : "";
  return (
    `Today's reading is ${gap} ${dir} your trend — likely normal day-to-day ` +
    `fluctuation${tail}. Your trend is what matters.`
  );
}

/**
 * Context note for the latest weigh-in card, or `null` when no note is warranted.
 *
 * Honest by construction: it states only what the data supports — the reading sits
 * above/below the smoothed trend, how that compares to the user's own recent noise,
 * and that short-term swings are expected. See `noteText` for the cause-attribution
 * rule the copy obeys.
 *
 * Two independent gates:
 *  1. `thresholdKg` — an absolute floor deciding whether a note appears at all.
 *  2. `deviationTier` — a relative, per-user measure deciding how emphatic it is.
 *
 * Returns `null` when either value is missing (no trend yet / no weigh-in) or the
 * absolute gap does not exceed `thresholdKg`; otherwise `{tier, text}`, where the
 * caller maps `tier` to the note's visual emphasis.
 *
 * @param {number|null} rawKg latest raw weigh-in, kg
 * @param {number|null} trendKg smoothed trend for the same day, kg
 * @param {"kg"|"lb"} units display unit for the gap value
 * @param {Array<number>} residualsKg raw − trend over the visible window, kg
 * @param {number} thresholdKg floor gate, in kg
 * @returns {{tier: "normal"|"notable"|"large", text: string}|null}
 */
export function fluctuationNote(
  rawKg,
  trendKg,
  units,
  residualsKg = [],
  thresholdKg = FLUCTUATION_NOTE_THRESHOLD_KG,
) {
  if (rawKg == null || trendKg == null) return null;
  const gapKg = rawKg - trendKg;
  if (Math.abs(gapKg) <= thresholdKg) return null;
  const gap = `${Math.abs(fromKg(gapKg, units)).toFixed(1)} ${unitLabel(units)}`;
  const tier = deviationTier(gapKg, residualsKg);
  return { tier, text: noteText(tier, gap, gapKg > 0) };
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
