/**
 * Weekly aggregation — pure, DOM-free bucketing of logged entries into ISO weeks
 * (Monday→Sunday) with the per-week aggregates the History screen shows.
 *
 * Extracted from history.js's `renderWeekly` (no behaviour change) so the bucketing
 * and averaging can be unit-tested in isolation; the screen now consumes
 * `aggregateWeeks` and only formats the result. Weights stay in kg and energies in
 * kcal (canonical units) — the display layer converts. Missing / declined values are
 * `null`, never a fabricated number.
 */

/** Mean of the finite (non-null, non-NaN) values; `null` when none qualify. */
export function mean(nums) {
  const vals = nums.filter((n) => n != null && !Number.isNaN(n));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

/** Monday (UTC) of the ISO week containing `iso`, as YYYY-MM-DD. */
export function weekStart(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const dow = (d.getUTCDay() + 6) % 7; // 0 = Monday … 6 = Sunday
  d.setUTCDate(d.getUTCDate() - dow);
  return d.toISOString().slice(0, 10);
}

/** `iso` shifted by `days` (UTC), as YYYY-MM-DD. */
export function addDaysIso(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * Bucket entries into ISO weeks and aggregate each.
 *
 * @param {{date: string, weight_kg: number|null, kcal: number|null}[]} entries
 * @param {Record<string, number|null>} trendByDate  smoothed trend_kg keyed by day
 * @param {Record<string, {tdee: number|null}>} tdeeByDate  rolling estimate by day
 * @returns week summaries, NEWEST FIRST, each:
 *   {weekStart, weekEnd, logged, avgWeightKg, avgIntake, avgTrendKg,
 *    netChangeKg, weeklyTdee, deficit}
 *   where any aggregate with no data is `null` (e.g. `weeklyTdee`/`deficit` when the
 *   estimator declined all week, `netChangeKg` with fewer than two trend points).
 */
export function aggregateWeeks(entries, trendByDate = {}, tdeeByDate = {}) {
  const byWeek = new Map();
  for (const e of entries) {
    const wk = weekStart(e.date);
    if (!byWeek.has(wk)) byWeek.set(wk, []);
    byWeek.get(wk).push(e);
  }

  const weeks = [...byWeek.keys()].sort((a, b) => (a < b ? 1 : -1)); // newest first
  return weeks.map((wk) => {
    const days = byWeek.get(wk);
    const avgIntake = mean(days.map((d) => d.kcal));

    // Trend values for this week's days (from /estimate), for the average and the
    // net change (first→last available trend within the week).
    const trends = days
      .map((d) => ({ date: d.date, t: trendByDate[d.date] }))
      .filter((x) => x.t != null)
      .sort((a, b) => (a.date < b.date ? -1 : 1));
    const netChangeKg = trends.length >= 2 ? trends[trends.length - 1].t - trends[0].t : null;

    // Weekly TDEE: the most recent rolling estimate within the week that the
    // estimator actually produced. `null` when it declined all week (early/pre-window).
    let weeklyTdee = null;
    for (const d of [...days].sort((a, b) => (a.date < b.date ? -1 : 1))) {
      const h = tdeeByDate[d.date];
      if (h != null && h.tdee != null) weeklyTdee = h.tdee;
    }
    const deficit = weeklyTdee != null && avgIntake != null ? weeklyTdee - avgIntake : null;

    return {
      weekStart: wk,
      weekEnd: addDaysIso(wk, 6),
      logged: days.length,
      avgWeightKg: mean(days.map((d) => d.weight_kg)),
      avgIntake,
      avgTrendKg: mean(trends.map((x) => x.t)),
      netChangeKg,
      weeklyTdee,
      deficit,
    };
  });
}
