/**
 * Pure onboarding / cold-start logic — no DOM, so it's unit-testable under
 * `node --test`. The dashboard (app.js) uses these to decide whether to show the
 * first-run panel and what honest copy to put in it.
 *
 * The whole point is honesty: the "when does the estimate arrive" message is driven
 * by the estimator's ACTUAL returned state and its real thresholds (from
 * /estimate/requirements), never a hardcoded guess that could drift from the math.
 */

/**
 * Decide the dashboard's onboarding state from an /estimate response.
 *
 *  - "empty"    → the user has logged nothing yet; lead with import.
 *  - "building" → some data, but the estimator still reports insufficient_data;
 *                 show progress toward a real estimate.
 *  - "ready"    → the estimator produced an estimate (ok or low_confidence);
 *                 the normal dashboard stands on its own, no onboarding panel.
 */
export function onboardingState(estimate) {
  if (!estimate || estimate.n_entries === 0) return "empty";
  if (estimate.tdee && estimate.tdee.status === "insufficient_data") return "building";
  return "ready";
}

/** True when the onboarding panel should be shown at all (empty or building). */
export function showOnboarding(estimate) {
  return onboardingState(estimate) !== "ready";
}

/**
 * Progress toward the estimator's minimum-data bar, for the "building" copy.
 * `requirements` is the /estimate/requirements payload. Counts come from the
 * estimate's own diagnostic fields, so "have" and "need" are always in sync with
 * what the estimator actually measured. `need` is clamped at 0 (never negative).
 */
export function onboardingProgress(estimate, requirements) {
  const tdee = (estimate && estimate.tdee) || {};
  const weightHave = tdee.n_valid_weight_days ?? 0;
  const intakeHave = tdee.n_valid_intake_days ?? 0;
  const weightTarget = (requirements && requirements.min_weight_days) ?? 0;
  const intakeTarget = (requirements && requirements.min_intake_days) ?? 0;
  return {
    windowDays: tdee.window_days ?? null,
    weightHave,
    weightTarget,
    weightNeed: Math.max(0, weightTarget - weightHave),
    intakeHave,
    intakeTarget,
    intakeNeed: Math.max(0, intakeTarget - intakeHave),
  };
}

/** True when all four demographic settings needed for the provisional are present.
 *  (Weight isn't here — it comes from the latest weigh-in, checked server-side.) */
export function demographicsComplete(settings) {
  if (!settings) return false;
  return (
    settings.age != null &&
    settings.sex != null &&
    settings.height_cm != null &&
    settings.activity_level != null
  );
}

/** True when the estimate carries a provisional (opt-in day-1 formula) number.
 *  The server only includes it during cold-start with complete demographics + a
 *  weigh-in, so the frontend just checks presence. */
export function hasProvisional(estimate) {
  return !!(estimate && estimate.provisional);
}

/**
 * What the cold-start card should render in its provisional slot. Called only while
 * onboarding is shown (state !== "ready"):
 *   - "provisional"      → show the generic day-1 estimate + its wide band + caveat.
 *   - "add_demographics" → no demographics yet; offer to add them for a rough start.
 *   - "needs_weight"     → demographics complete but no provisional came back, which
 *                          means there's no weigh-in yet to anchor the formula.
 */
export function coldStartProvisionalView(estimate, settings) {
  if (hasProvisional(estimate)) {
    const p = estimate.provisional;
    return { kind: "provisional", tdee: p.tdee, low: p.low, high: p.high, basis: p.basis };
  }
  if (!demographicsComplete(settings)) return { kind: "add_demographics" };
  return { kind: "needs_weight" };
}

/** Honest one-line progress sentence for the "building" state, from
 *  onboardingProgress() output. States what you have vs. the real threshold. */
export function onboardingProgressText(progress) {
  const window = progress.windowDays ? `the last ${progress.windowDays} days` : "your data";
  return (
    `In ${window} you've logged ${progress.weightHave} of ${progress.weightTarget} ` +
    `weigh-in days and ${progress.intakeHave} of ${progress.intakeTarget} intake days ` +
    "needed before the estimate is trustworthy."
  );
}

/**
 * Map an ImportResult ({ imported, warnings }) to display parts. Shared by the
 * dashboard onboarding panel and the Settings import so their feedback can't drift.
 *   - ok=false when nothing imported (the caller shows it as an error/notice).
 *   - warnings pass through verbatim — they're the parser's honest record of what
 *     it skipped and why.
 */
export function importResultMessage(result) {
  const imported = (result && result.imported) || 0;
  const warnings = (result && result.warnings) || [];
  if (imported === 0) {
    return { ok: false, summary: "Nothing was imported.", warnings };
  }
  const noun = imported === 1 ? "entry" : "entries";
  return {
    ok: true,
    summary:
      `Imported ${imported} ${noun}. ` +
      "The dashboard recomputes from the new data on its next load.",
    warnings,
  };
}
