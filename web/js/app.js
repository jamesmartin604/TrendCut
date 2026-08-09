/**
 * Dashboard app — loads /estimate + /settings, renders the hero, pace, chart and
 * metric cards, and drives the log-entry modal.
 *
 * Faithful status surfacing (the critical rule): the UI renders exactly what the
 * estimator returned. "insufficient_data" shows an honest empty hero while still
 * drawing whatever weight points exist; "low_confidence" shows the number visibly
 * flagged with its wide CI made prominent. No fallback TDEE is ever invented here.
 */

import {
  getEstimate,
  getRequirements,
  getSettings,
  getTdeeHistory,
  importEntries,
} from "./api.js";
import { goalLineAnchors, nearestIndex } from "./chart-math.js";
import { renderWeightChart } from "./chart.js";
import { addDays, fmtDateHuman, needsYear } from "./dates.js";
import { openEntryForm } from "./entry-form.js";
import { caveatsForState, formatDerivation } from "./derivation.js";
import {
  coldStartProvisionalView,
  importResultMessage,
  onboardingProgress,
  onboardingProgressText,
  onboardingState,
} from "./onboarding.js";
import { estimateStability, fluctuationNote, plateauLabel, tdeeHeroView } from "./status.js";
import { formatWeight, fromKg, unitLabel } from "./units.js";

const $ = (id) => document.getElementById(id);

// Opt-in goal line, remembered client-side only (localStorage — no API/schema
// change). Defaults OFF. Wrapped so a locked-down browser (storage disabled) still
// works, just without persistence.
const GOAL_PREF_KEY = "trendcut.goalLine";
function readGoalPref() {
  try {
    return localStorage.getItem(GOAL_PREF_KEY) === "1";
  } catch {
    return false;
  }
}
function writeGoalPref(on) {
  try {
    localStorage.setItem(GOAL_PREF_KEY, on ? "1" : "0");
  } catch {
    /* storage unavailable — toggle just won't persist this session */
  }
}

const state = {
  estimate: null,
  settings: null,
  requirements: null, // estimator's minimum-data bar (for onboarding copy)
  tdeeHistory: null, // rolling as-of-each-day estimates; drives the stability gate
  range: "30", // chart range in days, or "all"
  chartGeometry: null, // hit-test geometry from the last chart render (tooltip)
  goalLine: readGoalPref(), // show the plan/goal line on the weight chart
};

// Display unit preference (stored data stays kg; this only affects presentation).
const units = () => (state.settings?.units === "lb" ? "lb" : "kg");

// ---------- formatting helpers ---------- //

const fmtInt = (v) => Math.round(v).toLocaleString("en-US");

function todayIso() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

// ---------- hero: TDEE + plateau ---------- //

function renderHero() {
  const { tdee } = state.estimate;
  const valueEl = $("tdee-value");
  const ciEl = $("tdee-ci");
  const unitEl = $("tdee-unit");
  const lowConf = $("low-conf-chip");
  const noteEl = $("tdee-stability-note");
  const stability = estimateStability(state.tdeeHistory?.points ?? []);
  const view = tdeeHeroView(tdee, stability);

  noteEl.hidden = true;
  lowConf.className = "chip chip-warning";
  lowConf.textContent = "Low confidence";

  if (view.insufficient) {
    valueEl.textContent = "Not enough data yet";
    valueEl.classList.add("is-muted");
    unitEl.hidden = true;
    lowConf.hidden = true;
    ciEl.classList.remove("is-prominent");
    ciEl.textContent =
      `${tdee.n_valid_weight_days} weigh-ins · ${tdee.n_valid_intake_days} intake days ` +
      `in the last ${tdee.window_days} — keep logging and the estimate will appear.`;
    $("derivation").hidden = true; // no data-derived estimate → nothing to audit
    return;
  }

  valueEl.textContent = fmtInt(view.tdee);
  valueEl.classList.remove("is-muted");
  unitEl.hidden = !view.showUnit;

  if (view.unstable) {
    // The estimate has moved more than one headline number honestly supports. Show
    // the range it ACTUALLY occupied — an observed fact from the same /tdee-history
    // values the TDEE chart plots — rather than widening the estimator's interval by
    // some invented factor. The estimator's own CI stays in the derivation panel,
    // where the optimistic-interval caveat already explains it.
    ciEl.textContent =
      `${fmtInt(view.rangeLo)}–${fmtInt(view.rangeHi)} kcal · range over the last ` +
      `${view.rangeDays} days`;
    lowConf.className = "chip chip-settling";
    lowConf.textContent = "Settling";
    lowConf.hidden = false;
    noteEl.textContent =
      `Your estimate has been re-adjusting over the past ${view.rangeDays} days as older ` +
      "days leave the calculation window. Treat it as a broad range rather than a " +
      "single number — your weight trend is the steadier signal right now.";
    noteEl.hidden = false;
  } else {
    ciEl.textContent = `${fmtInt(view.ciLow)}–${fmtInt(view.ciHigh)} kcal · 95% CI`;
    lowConf.hidden = !view.lowConfidence;
  }

  // Low confidence: the wide CI is the story. Unsettled: the observed range is.
  // Both route to the same prominent line rather than a parallel treatment.
  ciEl.classList.toggle("is-prominent", view.ciProminent);

  renderDerivation(tdee, stability);
}

/**
 * "How we calculated this": the estimator's real chain with the real values that
 * produced the number above, the CI explained in plain terms, and the honest
 * caveats (including the unflattering ones). Everything is the estimator's own
 * output — the panel can't disagree with the headline.
 */
function renderDerivation(tdee, stability = null) {
  const panel = $("derivation");
  const info = formatDerivation(tdee);
  if (info == null) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  $("derivation-intro").textContent =
    "Your TDEE is your mean intake minus the energy your weight trend gained or lost " +
    `(from ${info.regressionPoints} weigh-ins and ${info.intakeDays} intake days in the ` +
    "window). In kilograms, the estimator's native units:";
  $("derivation-chain").textContent = info.chainText;

  if (info.ci) {
    $("derivation-ci").hidden = false;
    $("derivation-ci").textContent =
      `Confidence interval ${info.ci.text}: we're about 95% confident your true ` +
      `maintenance sits in this range. Its ±${fmtInt(info.ci.halfWidth)} kcal width comes ` +
      "from the uncertainty (standard error) in your weight-trend slope.";
  } else {
    $("derivation-ci").hidden = true;
  }

  const caveats = caveatsForState(
    tdee.status,
    tdee.low_confidence_reason,
    info.alpha,
    stability,
  );
  $("derivation-caveats").innerHTML = caveats
    .map((c) => `<li class="caveat-${c.kind.replace(/_/g, "-")}">${escapeHtml(c.text)}</li>`)
    .join("");
}

function renderPlateau() {
  const { plateau } = state.estimate;
  const { label, cls } = plateauLabel(plateau.state);

  const dot = $("plateau-dot");
  const text = $("plateau-state");
  dot.className = `status-dot ${cls}`;
  text.className = `status-text ${cls}`;
  text.textContent = label;
  $("plateau-explain").textContent = plateau.explanation;
}

// ---------- pace card ---------- //

function lastTrendPoint() {
  const { trend } = state.estimate;
  for (let i = trend.length - 1; i >= 0; i -= 1) {
    if (trend[i].trend_kg != null) return trend[i];
  }
  return null;
}

/** First day whose smoothed trend is defined — the goal line's start anchor. */
function firstTrendPoint() {
  const { trend } = state.estimate;
  for (let i = 0; i < trend.length; i += 1) {
    if (trend[i].trend_kg != null) return trend[i];
  }
  return null;
}

/** A goal line is possible only once both a target weight and date are set. */
function hasTarget() {
  const s = state.settings;
  return s != null && s.target_weight != null && s.target_date != null;
}

function renderPace() {
  const { tdee } = state.estimate;
  const settings = state.settings;
  const deficitValue = $("deficit-value");
  const deficitLabel = $("deficit-label");
  const bar = $("deficit-bar");
  const rate = $("rate-value");
  const chip = $("pace-chip");
  const chipText = $("pace-chip-text");

  chip.className = "pace-chip";

  if (tdee.status === "insufficient_data") {
    deficitValue.textContent = "–";
    deficitLabel.textContent = "Daily deficit";
    bar.style.width = "0";
    rate.textContent = "–";
    chipText.textContent = "Pace appears once there's a TDEE estimate.";
    return;
  }

  // Implied balance: TDEE − mean intake (> 0 means a deficit).
  const deficit = tdee.tdee - tdee.mean_intake;
  deficitLabel.textContent = deficit >= 0 ? "Daily deficit" : "Daily surplus";
  deficitValue.textContent = fmtInt(Math.abs(deficit));
  const expected = settings.expected_deficit_kcal || 0;
  const frac = expected > 0 ? Math.max(0, Math.min(deficit / expected, 1)) : 0;
  bar.style.width = `${Math.round(frac * 100)}%`;

  const u = units();
  const weeklyKg = tdee.slope_kg_per_day * 7;
  const weeklyDisp = Math.abs(fromKg(weeklyKg, u));
  rate.textContent = `${weeklyKg >= 0 ? "+" : "−"}${weeklyDisp.toFixed(1)} ${unitLabel(u)}/week`;

  // On-pace-for-target line — simple arithmetic on the estimator's slope; this
  // projects, it never invents an estimate. Projection math stays in kg; only the
  // displayed target converts to the chosen unit.
  const target = settings.target_weight;
  const last = lastTrendPoint();
  if (target == null || last == null) {
    chipText.textContent = "No target set.";
    return;
  }
  const tgt = `${formatWeight(target, u)} ${unitLabel(u)}`;
  const remaining = last.trend_kg - target;
  if (remaining <= 0) {
    chip.classList.add("is-ontrack");
    chipText.textContent = `Target ${tgt} reached.`;
    return;
  }
  if (tdee.slope_kg_per_day >= 0) {
    chip.classList.add("is-behind");
    chipText.textContent = `Trend isn't moving toward ${tgt} right now.`;
    return;
  }
  const daysToTarget = Math.ceil(remaining / -tdee.slope_kg_per_day);
  const arrival = addDays(last.date, daysToTarget);
  const wanted = settings.target_date
    ? new Date(`${settings.target_date}T00:00:00Z`)
    : null;

  // Both halves of this sentence share ONE year decision. The projected arrival is
  // unbounded (a shallow trend projects years out) and the target is user-set, so
  // either can leave the current year; formatting them independently produced
  // "Projected … by Jul 9 — after your Aug 9 target", which reads as a contradiction
  // even though the comparison below is correct and uses full dates.
  const withYear = needsYear([arrival, wanted]);
  const arrivalText = fmtDateHuman(arrival, { withYear });

  if (wanted) {
    if (arrival <= wanted) {
      chip.classList.add("is-ontrack");
      chipText.textContent = `On pace for ${tgt} by ${arrivalText}.`;
    } else {
      chip.classList.add("is-behind");
      chipText.textContent =
        `Projected ${tgt} by ${arrivalText} — after your ` +
        `${fmtDateHuman(wanted, { withYear })} target.`;
    }
  } else {
    chipText.textContent = `Projected to reach ${tgt} by ${arrivalText}.`;
  }
}

// ---------- metric cards ---------- //

function renderMetrics() {
  const { tdee } = state.estimate;
  const last = lastTrendPoint();

  if (last != null) {
    $("trend-weight").textContent = formatWeight(last.trend_kg, units());
    $("trend-weight-unit").textContent = ` ${unitLabel(units())}`;
    $("trend-weight-unit").hidden = false;
    $("trend-weight-note").textContent = `Smoothed as of ${fmtDateHuman(
      new Date(`${last.date}T00:00:00Z`),
    )}`;
  } else {
    $("trend-weight").textContent = "–";
    $("trend-weight-unit").hidden = true;
    $("trend-weight-note").textContent = "No weigh-ins yet";
  }

  if (tdee.mean_intake != null) {
    $("mean-intake").textContent = fmtInt(tdee.mean_intake);
    $("mean-intake-unit").hidden = false;
    $("mean-intake-note").textContent = `Daily average over the last ${tdee.window_days} days`;
  } else {
    $("mean-intake").textContent = "–";
    $("mean-intake-unit").hidden = true;
    $("mean-intake-note").textContent = `${tdee.n_valid_intake_days} intake days logged in window`;
  }
}

// ---------- latest raw weigh-in card ---------- //

/**
 * Raw-vs-trend residuals (kg) over the window the chart is currently showing —
 * the same slice the user can see, so "your recent range" means the range in front
 * of them. Days without a weigh-in (calorie-only) contribute no residual.
 *
 * Derived entirely from data already on the dashboard: the raw points and the EWMA
 * trend the chart already plots. No estimator change, no extra API field.
 */
function visibleResiduals() {
  return chartPoints()
    .filter((p) => p.weight_kg != null && p.trend_kg != null)
    .map((p) => p.weight_kg - p.trend_kg);
}

function renderRawWeightCard() {
  const u = units();
  const trend = state.estimate.trend;
  const rawEl = $("raw-value");
  const unitEl = $("raw-unit");
  const noteEl = $("raw-note");
  const contextEl = $("raw-context");
  contextEl.hidden = true; // shown only when a fluctuation note is warranted
  contextEl.className = "metric-note"; // drop any tier emphasis from a previous render

  // "Current" means the most recent logged day. If that day has no weigh-in
  // (calorie-only) or there's no data at all, show an honest empty state rather
  // than surfacing a stale older number as if it were today's.
  const latest = trend.length ? trend[trend.length - 1] : null;
  if (!latest || latest.weight_kg == null) {
    rawEl.textContent = "No recent weigh-in";
    rawEl.classList.add("is-muted");
    unitEl.hidden = true;
    noteEl.textContent = latest
      ? `${fmtDateHuman(new Date(`${latest.date}T00:00:00Z`))} was a calorie-only day`
      : "Log a weight to see it here";
    return;
  }

  rawEl.textContent = formatWeight(latest.weight_kg, u);
  rawEl.classList.remove("is-muted");
  unitEl.textContent = ` ${unitLabel(u)}`;
  unitEl.hidden = false;

  const dateStr = fmtDateHuman(new Date(`${latest.date}T00:00:00Z`));
  // Trend comparison only with >= 2 weigh-ins — a single seed's trend equals the
  // raw value and would be a meaningless "0 above trend".
  const weighIns = trend.reduce((n, p) => n + (p.weight_kg != null ? 1 : 0), 0);
  if (latest.trend_kg != null && weighIns >= 2) {
    const ul = unitLabel(u);
    const trendDisp = formatWeight(latest.trend_kg, u);
    const diff = fromKg(latest.weight_kg - latest.trend_kg, u);
    if (Math.abs(diff) < 0.05) {
      noteEl.textContent = `${dateStr} · trend ${trendDisp} ${ul} · on trend`;
    } else {
      const sign = diff > 0 ? "+" : "−";
      const dir = diff > 0 ? "above" : "below";
      noteEl.textContent =
        `${dateStr} · trend ${trendDisp} ${ul} · ${sign}${Math.abs(diff).toFixed(1)} ${dir} trend`;
    }
    // Reassurance note for a notable (threshold-gated) raw-vs-trend gap. Its
    // emphasis scales with how large today's gap is relative to the user's OWN
    // recent noise, measured over the visible chart window (`visibleResiduals`) —
    // so a routine swing stays muted and an unusual one is flagged, both still
    // framed as noise rather than a stall. Pure helper decides tier and wording.
    const note = fluctuationNote(latest.weight_kg, latest.trend_kg, u, visibleResiduals());
    if (note != null) {
      // The lead goes in its own <strong> so the emphasis can land on the fact while
      // the reassurance stays at normal weight (the CSS only bolds it on an
      // emphasised tier). Built as nodes, not innerHTML — the copy is app-authored,
      // but it interpolates a formatted number, so it never becomes markup.
      const lead = document.createElement("strong");
      lead.textContent = note.lead;
      contextEl.replaceChildren(lead, document.createTextNode(` ${note.rest}`));
      if (note.tier !== "normal") contextEl.classList.add(`is-${note.tier}`);
      contextEl.hidden = false;
    }
  } else {
    noteEl.textContent = `${dateStr} · trend needs another weigh-in`;
  }
}

// ---------- chart tooltip (hover / tap) ---------- //

function hideChartTooltip() {
  $("chart-tooltip").hidden = true;
  $("chart-cursor").hidden = true;
}

/** Nearest point that actually has a raw weigh-in, scanning out from `idx`. */
function nearestWeighIn(points, idx) {
  for (let d = 0; d < points.length; d += 1) {
    const before = points[idx - d];
    if (before && before.weight != null) return before;
    const after = points[idx + d];
    if (after && after.weight != null) return after;
  }
  return null;
}

function updateChartTooltip(clientX) {
  const geom = state.chartGeometry;
  const el = $("chart");
  if (!geom) return hideChartTooltip();
  const rect = el.getBoundingClientRect();
  if (rect.width === 0) return hideChartTooltip();

  // Map the cursor back to viewBox space (== chart pixel space at render time).
  const vbX = (clientX - rect.left) * (geom.width / rect.width);
  const idx = nearestIndex(vbX, geom.x0, geom.x1, geom.points.length);
  if (idx < 0) return hideChartTooltip();
  const near = geom.points[idx];
  const p = near.weight != null ? near : nearestWeighIn(geom.points, idx);
  if (!p) return hideChartTooltip();

  // Only show when the cursor is genuinely near the point's column.
  if (Math.abs(p.xPos - vbX) > 30) return hideChartTooltip();

  const ul = unitLabel(units());
  const tip = $("chart-tooltip");
  const trendTxt = p.trend == null ? "—" : `${p.trend.toFixed(1)} ${ul}`;
  tip.innerHTML =
    `<div class="tt-date">${fmtDateHuman(new Date(`${p.date}T00:00:00Z`))}</div>` +
    `<div class="tt-row"><span class="tt-k">raw</span><span class="tt-v">${p.weight.toFixed(1)} ${ul}</span></div>` +
    `<div class="tt-row"><span class="tt-k tt-k-trend">trend</span><span class="tt-v">${trendTxt}</span></div>`;
  tip.hidden = false;

  const cursor = $("chart-cursor");
  cursor.style.left = `${p.xPos}px`;
  cursor.style.top = `${p.yWeight}px`;
  cursor.hidden = false;

  // Center above the point; flip below near the top edge; clamp within the chart.
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  let left = p.xPos - tw / 2;
  left = Math.max(4, Math.min(left, geom.width - tw - 4));
  let top = p.yWeight - th - 12;
  if (top < 4) top = p.yWeight + 14;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function wireChartTooltip() {
  const el = $("chart");
  const move = (e) => updateChartTooltip(e.clientX);
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerdown", move); // touch tap / mouse click
  el.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") hideChartTooltip();
  });
  // Tapping/clicking outside the chart dismisses the tooltip (touch).
  document.addEventListener("pointerdown", (e) => {
    if (!el.contains(e.target)) hideChartTooltip();
  });
}

// ---------- chart ---------- //

function chartPoints() {
  const points = state.estimate.trend;
  if (state.range === "all") return points;
  return points.slice(-Number(state.range));
}

function renderChart() {
  const u = units();
  // Convert the kg series into the display unit before drawing; the chart math is
  // unit-agnostic, so this is the only place units enter the chart.
  const disp = chartPoints().map((p) => ({
    date: p.date,
    weight_kg: fromKg(p.weight_kg, u),
    trend_kg: fromKg(p.trend_kg, u),
  }));

  // Goal (plan) line — only when toggled on AND a target exists AND there's a
  // trend-start to anchor to. Anchors are computed by a pure function on the FULL
  // trend series (not the visible slice) and the stored target, then converted to
  // the display unit; when hidden or unavailable, `goal` is null and the chart
  // frames to the data as usual.
  let goal = null;
  const start = state.estimate.trend.length ? firstTrendPoint() : null;
  if (state.goalLine && hasTarget() && start != null && disp.length) {
    goal = goalLineAnchors({
      displayStartIso: disp[0].date,
      firstLoggedIso: start.date,
      startWeight: fromKg(start.trend_kg, u),
      targetIso: state.settings.target_date,
      targetWeight: fromKg(state.settings.target_weight, u),
    });
  }

  state.chartGeometry = renderWeightChart($("chart"), {
    points: disp,
    targetWeight: fromKg(state.settings.target_weight, u),
    unitLabel: unitLabel(u),
    goal,
    goalTargetIso: goal ? state.settings.target_date : null,
  });
  hideChartTooltip(); // a re-render (resize / range change) invalidates any open tooltip
  $("chart-subtitle").textContent =
    state.range === "all" ? "All logged days" : `Last ${state.range} days`;
}

// ---------- goal-line toggle ---------- //

/**
 * Reflect target availability into the toggle: enabled + checked-state when a
 * target exists, otherwise disabled with a "set a target" hint. Called after each
 * refresh so a target saved in Settings lights the control up on return.
 */
function syncGoalToggle() {
  const input = $("goal-toggle");
  const label = $("goal-toggle-label");
  const text = label.querySelector(".switch-text");
  const enabled = hasTarget();

  input.disabled = !enabled;
  label.classList.toggle("is-disabled", !enabled);
  if (enabled) {
    input.checked = state.goalLine;
    text.textContent = "Goal line";
    label.title = "Show a plan line from your starting trend to your target";
  } else {
    input.checked = false;
    text.textContent = "Goal line — set a target first";
    label.title = "Set a target weight and date in Settings to plan a goal line";
  }
}

function wireGoalToggle() {
  $("goal-toggle").addEventListener("change", (e) => {
    state.goalLine = e.target.checked;
    writeGoalPref(state.goalLine);
    renderChart(); // redraw immediately; extends/reframes the x-domain to match
  });
}

function wireRangeButtons() {
  for (const btn of document.querySelectorAll(".range-btn")) {
    btn.addEventListener("click", () => {
      state.range = btn.dataset.range;
      for (const b of document.querySelectorAll(".range-btn")) {
        b.classList.toggle("is-active", b === btn);
      }
      renderChart();
      // The weigh-in card's fluctuation note is classified against the VISIBLE
      // window's noise, so a range switch changes what counts as unusual — re-render
      // it too, or the note keeps a tier computed from the previous range.
      renderRawWeightCard();
    });
  }
}

// ---------- log entry (shared entry-form modal) ---------- //

function wireLogButtons() {
  // New entry: today's date, editable; refresh the dashboard after a save so the
  // hero/chart/plateau recompute.
  const open = () =>
    openEntryForm({ date: todayIso(), units: units(), dateEditable: true, onSaved: refresh });
  $("open-log-btn").addEventListener("click", open);
  $("open-log-fab").addEventListener("click", open);
}

// ---------- onboarding (first-run empty / low-data state) ---------- //

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// CSV text held between file-pick and the explicit confirm click (import upserts
// by date and can't be undone, so the POST never fires on selection alone).
let pendingOnboardImport = null;

function resetOnboardingImportUi() {
  pendingOnboardImport = null;
  $("onboarding-confirm").hidden = true;
  $("onboarding-file").value = ""; // re-selecting the same file must re-fire `change`
}

function renderOnboarding() {
  const section = $("onboarding");
  const stateName = onboardingState(state.estimate);
  if (stateName === "ready") {
    section.hidden = true;
    resetOnboardingImportUi();
    return;
  }
  section.hidden = false;

  const title = $("onboarding-title");
  const lead = $("onboarding-lead");
  const progress = $("onboarding-progress");
  if (stateName === "empty") {
    title.textContent = "Welcome to TrendCut";
    lead.textContent =
      "TrendCut estimates your real maintenance calories from your weight and intake — " +
      "but only once there's enough consistent data to trust the number. Importing your " +
      "history is the fastest way to a real estimate.";
    progress.hidden = true;
  } else {
    // "building": some data, but the estimator still reports insufficient_data.
    title.textContent = "You're on your way";
    lead.textContent =
      "Your estimate appears once there's enough consistent data — no fabricated number " +
      "before then. Importing past history is the fastest way to get there.";
    progress.textContent = onboardingProgressText(
      onboardingProgress(state.estimate, state.requirements),
    );
    progress.hidden = false;
  }

  renderProvisional();
}

/**
 * The opt-in day-1 provisional, shown inside the onboarding card during cold-start.
 * It is styled distinctly and labelled as a generic, non-data-derived estimate; the
 * server only sends it below the real threshold, and it's gone at handoff. When it's
 * absent we instead prompt for whatever's missing (demographics, or a weigh-in).
 */
function renderProvisional() {
  const block = $("onboarding-provisional");
  const demoPrompt = $("onboarding-demographics-prompt");
  const weightPrompt = $("onboarding-weight-prompt");
  block.hidden = true;
  demoPrompt.hidden = true;
  weightPrompt.hidden = true;

  const view = coldStartProvisionalView(state.estimate, state.settings);
  if (view.kind === "provisional") {
    $("provisional-tdee").textContent = fmtInt(view.tdee);
    $("provisional-band").textContent =
      `Wide range: ${fmtInt(view.low)}–${fmtInt(view.high)} kcal/day — a generic ` +
      "population estimate, not your data.";
    $("provisional-caveat").textContent = view.basis;
    // Matching (small) transparency for the provisional: how this generic number is
    // computed. Deliberately lighter than the data-derived derivation panel.
    $("provisional-method").textContent =
      "How this is computed: resting metabolic rate from the Mifflin–St Jeor formula " +
      "(your age, sex, height and latest weight) × an activity multiplier, with a wide " +
      "±25% population band.";
    block.hidden = false;
  } else if (view.kind === "add_demographics") {
    demoPrompt.hidden = false;
  } else {
    weightPrompt.hidden = false;
  }
}

async function onOnboardingFilePicked(file) {
  $("onboarding-summary").hidden = true;
  $("onboarding-warnings").hidden = true;
  $("onboarding-error").hidden = true;
  if (!file) return;
  try {
    pendingOnboardImport = await file.text();
  } catch (err) {
    $("onboarding-error").textContent = `Couldn't read the file: ${err.message}`;
    $("onboarding-error").hidden = false;
    return;
  }
  $("onboarding-confirm-text").textContent =
    `Import "${file.name}"? Rows are matched by date — a date you've already logged is ` +
    "overwritten by the file's value, and there's no undo. New dates are added.";
  $("onboarding-confirm").hidden = false;
}

async function runOnboardingImport() {
  const btn = $("onboarding-confirm-btn");
  btn.disabled = true;
  btn.textContent = "Importing…";
  try {
    const result = await importEntries(pendingOnboardImport);
    renderOnboardImportResult(result);
    // Recompute the whole dashboard from the new data: this re-renders onboarding,
    // which hides the panel automatically if there's now enough for a real estimate.
    await refresh();
  } catch (err) {
    $("onboarding-error").textContent =
      `Import failed: ${err.message}. Nothing may have been saved — check History.`;
    $("onboarding-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Import";
    resetOnboardingImportUi();
  }
}

function renderOnboardImportResult(result) {
  const { ok, summary, warnings } = importResultMessage(result);
  if (ok) {
    $("onboarding-summary").textContent = summary;
    $("onboarding-summary").hidden = false;
  } else {
    $("onboarding-error").textContent = summary;
    $("onboarding-error").hidden = false;
  }
  if (warnings.length > 0) {
    $("onboarding-warnings").innerHTML = warnings
      .map((w) => `<span>${escapeHtml(w)}</span>`)
      .join("");
    $("onboarding-warnings").hidden = false;
  }
}

function wireOnboarding() {
  $("onboarding-import-btn").addEventListener("click", () => $("onboarding-file").click());
  $("onboarding-file").addEventListener("change", (e) => onOnboardingFilePicked(e.target.files[0]));
  $("onboarding-confirm-btn").addEventListener("click", runOnboardingImport);
  $("onboarding-cancel-btn").addEventListener("click", resetOnboardingImportUi);
  $("onboarding-log-btn").addEventListener("click", () =>
    openEntryForm({ date: todayIso(), units: units(), dateEditable: true, onSaved: refresh }),
  );
}

// ---------- load + render ---------- //

function renderAll() {
  renderOnboarding();
  renderHero();
  renderPlateau();
  renderPace();
  renderRawWeightCard();
  renderMetrics();
  syncGoalToggle();
  renderChart();
}

async function refresh() {
  // `/tdee-history` recomputes the estimator once per logged day, so it is the
  // heaviest of the four; fetched in parallel so it costs latency, not serial time.
  const [estimate, settings, requirements, tdeeHistory] = await Promise.all([
    getEstimate(),
    getSettings(),
    getRequirements(),
    getTdeeHistory(),
  ]);
  state.estimate = estimate;
  state.settings = settings;
  state.requirements = requirements;
  state.tdeeHistory = tdeeHistory;
  renderAll();
}

async function init() {
  wireLogButtons();
  wireRangeButtons();
  wireGoalToggle();
  wireChartTooltip();
  wireOnboarding();

  // Redraw the chart when its box resizes (responsive, PRD §8).
  const chartEl = $("chart");
  let lastWidth = 0;
  new ResizeObserver((entries) => {
    const width = entries[0].contentRect.width;
    if (state.estimate && Math.abs(width - lastWidth) > 1) {
      lastWidth = width;
      renderChart();
    }
  }).observe(chartEl);

  try {
    await refresh();
  } catch (err) {
    const el = $("page-error");
    el.textContent = `Couldn't reach the API: ${err.message}`;
    el.hidden = false;
  }
}

init();
