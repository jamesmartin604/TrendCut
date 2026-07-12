/**
 * Pure display logic for the "how we calculated this" transparency panel — no DOM,
 * so it's unit-testable under `node --test`.
 *
 * Honesty rules baked in here:
 *  - The chain is computed from the estimator's FULL-PRECISION values (the exposed
 *    `derivation` sub-object); we round only for display.
 *  - We show enough slope precision that the *visible* arithmetic lands on the
 *    *visible* TDEE and use "=". When display rounding means it can't land exactly,
 *    we use "≈" rather than pretending rounded inputs produce the exact output.
 *  - The CI half-width is taken straight from the returned interval.
 */

const _fmt = (n) => Math.round(n).toLocaleString("en-US");

/**
 * Build the derivation display from a TdeeResult that carries a `derivation`.
 * Returns null when there's nothing to show (no data-derived estimate).
 *
 * TDEE = mean_intake − slope × energy_density. The slope is in kg/day and the
 * energy density is per kg (the estimator's native units), so the chain is shown in
 * kg regardless of the user's kg/lb display preference — converting would create a
 * parallel calculation that could drift from the headline.
 */
export function formatDerivation(tdeeResult) {
  if (!tdeeResult || tdeeResult.tdee == null || !tdeeResult.derivation) return null;
  const d = tdeeResult.derivation;

  const intakeDisp = Math.round(d.mean_intake_kcal);
  const edDisp = Math.round(d.energy_density);
  const tdeeDisp = Math.round(tdeeResult.tdee);

  // Pick the smallest slope precision (3–6 dp) at which the shown arithmetic lands
  // exactly on the shown TDEE; if none does (display rounding of intake/tdee), fall
  // back to 4 dp and flag it approximate.
  let slopeDp = 4;
  let exact = false;
  for (let dp = 3; dp <= 6; dp += 1) {
    const sd = Number(d.slope_kg_per_day.toFixed(dp));
    if (Math.round(intakeDisp - sd * edDisp) === tdeeDisp) {
      slopeDp = dp;
      exact = true;
      break;
    }
  }
  const slopeDisp = Number(d.slope_kg_per_day.toFixed(slopeDp));
  const connector = exact ? "=" : "≈";

  // "− (slope × ed)" with a signed slope, so a negative (weight-loss) slope reads as
  // adding energy back — matching the literal formula the estimator uses.
  const chainText =
    `${_fmt(intakeDisp)} − (${slopeDisp.toFixed(slopeDp)} × ${_fmt(edDisp)}) ` +
    `${connector} ${_fmt(tdeeDisp)} kcal/day`;

  const half = tdeeResult.ci_high != null ? Math.round(tdeeResult.ci_high - tdeeResult.tdee) : null;
  const ciText =
    tdeeResult.ci_low != null
      ? `${_fmt(tdeeResult.ci_low)}–${_fmt(tdeeResult.ci_high)} kcal/day (95% CI)`
      : null;

  return {
    intakeDisp,
    slopeDisp,
    slopeDp,
    edDisp,
    tdeeDisp,
    connector,
    exact,
    chainText,
    intakeDays: tdeeResult.n_valid_intake_days,
    regressionPoints: d.n_regression_points,
    alpha: d.alpha,
    ci: tdeeResult.ci_low == null ? null : { low: tdeeResult.ci_low, high: tdeeResult.ci_high, halfWidth: half, text: ciText },
  };
}

// Caveat copy. Kept as constants so the tests can assert the exact honest wording.
const CAVEAT = {
  optimisticCI:
    "This interval is likely optimistic — narrower than reality. It's an ordinary " +
    "least-squares interval fit on an already-smoothed weight series, which " +
    "understates the true uncertainty. Read it as indicative, not exact.",
  energyDensity:
    "The 7,700 kcal/kg conversion is a population average. Real weight change mixes " +
    "fat, lean tissue and water, so your personal figure can differ.",
  smoothingLag: (alpha) =>
    `Your weigh-ins are smoothed (EWMA, α=${alpha}) before the trend is fit. Smoothing ` +
    "removes day-to-day water noise but lags genuine changes by a few days.",
  whyLowWide:
    "Confidence is low because your weight-trend slope is uncertain over this window — " +
    "the weigh-ins are noisy or sparse, so the interval is wide.",
  whyLowImplausible:
    "Confidence is low because the resulting number falls outside the physiologically " +
    "plausible range — usually a logging gap or an unusual intake/weight window.",
};

/**
 * Which caveats/explanations apply for a given estimator state. Returns an array of
 * { kind, text }. The optimistic-CI, energy-density and smoothing caveats apply
 * whenever a CI is shown — i.e. for BOTH `ok` and `low_confidence`. Below threshold
 * (`insufficient_data`) there's no data-derived estimate, so this returns [] and the
 * frontend shows the provisional's basis instead.
 */
export function caveatsForState(status, lowConfidenceReason, alpha) {
  if (status !== "ok" && status !== "low_confidence") return [];
  const out = [];
  if (status === "low_confidence") {
    const wide = lowConfidenceReason === "wide_interval" || lowConfidenceReason === "wide_interval_and_implausible";
    const implausible =
      lowConfidenceReason === "implausible_value" || lowConfidenceReason === "wide_interval_and_implausible";
    if (wide) out.push({ kind: "why_low_confidence", text: CAVEAT.whyLowWide });
    if (implausible) out.push({ kind: "why_low_confidence", text: CAVEAT.whyLowImplausible });
  }
  out.push({ kind: "optimistic_ci", text: CAVEAT.optimisticCI });
  out.push({ kind: "energy_density", text: CAVEAT.energyDensity });
  out.push({ kind: "smoothing_lag", text: CAVEAT.smoothingLag(alpha) });
  return out;
}
