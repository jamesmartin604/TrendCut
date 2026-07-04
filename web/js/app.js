/**
 * Dashboard app — loads /estimate + /settings, renders the hero, pace, chart and
 * metric cards, and drives the log-entry modal.
 *
 * Faithful status surfacing (the critical rule): the UI renders exactly what the
 * estimator returned. "insufficient_data" shows an honest empty hero while still
 * drawing whatever weight points exist; "low_confidence" shows the number visibly
 * flagged with its wide CI made prominent. No fallback TDEE is ever invented here.
 */

import { getEntry, getEstimate, getSettings, putEntry } from "./api.js";
import { renderWeightChart } from "./chart.js";
import { formatWeight, fromKg, toKg, unitLabel } from "./units.js";

const $ = (id) => document.getElementById(id);

const state = {
  estimate: null,
  settings: null,
  range: "30", // chart range in days, or "all"
};

// Display unit preference (stored data stays kg; this only affects presentation).
const units = () => (state.settings?.units === "lb" ? "lb" : "kg");

// ---------- formatting helpers ---------- //

const fmtInt = (v) => Math.round(v).toLocaleString("en-US");

function fmtDateHuman(date) {
  return date.toLocaleString("en", { month: "short", day: "numeric", timeZone: "UTC" });
}

function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d;
}

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

  if (tdee.status === "insufficient_data") {
    valueEl.textContent = "Not enough data yet";
    valueEl.classList.add("is-muted");
    unitEl.hidden = true;
    lowConf.hidden = true;
    ciEl.classList.remove("is-prominent");
    ciEl.textContent =
      `${tdee.n_valid_weight_days} weigh-ins · ${tdee.n_valid_intake_days} intake days ` +
      `in the last ${tdee.window_days} — keep logging and the estimate will appear.`;
    return;
  }

  valueEl.textContent = fmtInt(tdee.tdee);
  valueEl.classList.remove("is-muted");
  unitEl.hidden = false;
  ciEl.textContent = `${fmtInt(tdee.ci_low)}–${fmtInt(tdee.ci_high)} kcal · 95% CI`;

  const isLow = tdee.status === "low_confidence";
  lowConf.hidden = !isLow;
  // Low confidence: the wide CI is the story — make it the prominent line.
  ciEl.classList.toggle("is-prominent", isLow);
}

const PLATEAU_LABEL = {
  on_track: ["On track", "is-ontrack"],
  slowing: ["Slowing", "is-slowing"],
  stalled: ["Stalled", "is-stalled"],
  undetermined: ["Gathering data", "is-undetermined"],
};

function renderPlateau() {
  const { plateau } = state.estimate;
  const [label, cls] = PLATEAU_LABEL[plateau.state] ?? ["–", "is-undetermined"];

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
  const arrivalText = fmtDateHuman(arrival);
  if (settings.target_date) {
    const wanted = new Date(`${settings.target_date}T00:00:00Z`);
    if (arrival <= wanted) {
      chip.classList.add("is-ontrack");
      chipText.textContent = `On pace for ${tgt} by ${arrivalText}.`;
    } else {
      chip.classList.add("is-behind");
      chipText.textContent =
        `Projected ${tgt} by ${arrivalText} — after your ` +
        `${fmtDateHuman(wanted)} target.`;
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
  renderWeightChart($("chart"), {
    points: disp,
    targetWeight: fromKg(state.settings.target_weight, u),
    unitLabel: unitLabel(u),
  });
  $("chart-subtitle").textContent =
    state.range === "all" ? "All logged days" : `Last ${state.range} days`;
}

function wireRangeButtons() {
  for (const btn of document.querySelectorAll(".range-btn")) {
    btn.addEventListener("click", () => {
      state.range = btn.dataset.range;
      for (const b of document.querySelectorAll(".range-btn")) {
        b.classList.toggle("is-active", b === btn);
      }
      renderChart();
    });
  }
}

// ---------- log entry modal ---------- //

const modal = {
  overlay: null,
  form: null,

  open() {
    this.overlay.hidden = false;
    $("log-error").hidden = true;
    $("log-warnings").hidden = true;
    $("log-save-btn").disabled = false;
    $("log-save-btn").textContent = "Save entry";
    $("log-weight-unit").textContent = unitLabel(units());
    $("log-date").value = todayIso();
    $("log-weight").value = "";
    $("log-kcal").value = "";
    this.prefill($("log-date").value);
    $("log-weight").focus();
  },

  close() {
    this.overlay.hidden = true;
  },

  /** Pull any existing entry for the date so re-logging edits, not surprises. */
  async prefill(date) {
    try {
      const entry = await getEntry(date);
      // Only apply if the user hasn't switched dates while we fetched.
      if ($("log-date").value !== date) return;
      $("log-weight").value =
        entry?.weight_kg == null ? "" : formatWeight(entry.weight_kg, units());
      $("log-kcal").value = entry?.kcal ?? "";
    } catch {
      /* prefill is best-effort; saving still works */
    }
  },

  async save() {
    const date = $("log-date").value;
    const weightRaw = $("log-weight").value.trim();
    const kcalRaw = $("log-kcal").value.trim();
    const errorEl = $("log-error");
    const warningsEl = $("log-warnings");
    errorEl.hidden = true;
    warningsEl.hidden = true;

    if (!date) {
      errorEl.textContent = "Pick a date.";
      errorEl.hidden = false;
      return;
    }
    if (weightRaw === "" && kcalRaw === "") {
      errorEl.textContent = "Enter a weight, calories, or both.";
      errorEl.hidden = false;
      return;
    }

    const saveBtn = $("log-save-btn");
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      const saved = await putEntry(date, {
        weight_kg: weightRaw === "" ? null : toKg(Number(weightRaw), units()),
        kcal: kcalRaw === "" ? null : Math.round(Number(kcalRaw)),
      });
      await refresh();
      if (saved.warnings.length > 0) {
        // Soft input guards: the entry IS saved — surface, don't block.
        warningsEl.innerHTML = saved.warnings
          .map((w) => `<span>Saved with a warning: ${escapeHtml(w)}</span>`)
          .join("");
        warningsEl.hidden = false;
        saveBtn.disabled = false;
        saveBtn.textContent = "Save entry";
      } else {
        this.close();
      }
    } catch (err) {
      errorEl.textContent = `Couldn't save: ${err.message}`;
      errorEl.hidden = false;
      saveBtn.disabled = false;
      saveBtn.textContent = "Save entry";
    }
  },
};

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function wireModal() {
  modal.overlay = $("log-overlay");
  modal.form = $("log-form");

  $("open-log-btn").addEventListener("click", () => modal.open());
  $("open-log-fab").addEventListener("click", () => modal.open());
  $("log-close-btn").addEventListener("click", () => modal.close());
  $("log-cancel-btn").addEventListener("click", () => modal.close());
  $("log-date").addEventListener("change", (e) => modal.prefill(e.target.value));
  modal.overlay.addEventListener("click", (e) => {
    if (e.target === modal.overlay) modal.close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.overlay.hidden) modal.close();
  });
  modal.form.addEventListener("submit", (e) => {
    e.preventDefault();
    modal.save();
  });
}

// ---------- load + render ---------- //

function renderAll() {
  renderHero();
  renderPlateau();
  renderPace();
  renderMetrics();
  renderChart();
}

async function refresh() {
  const [estimate, settings] = await Promise.all([getEstimate(), getSettings()]);
  state.estimate = estimate;
  state.settings = settings;
  renderAll();
}

async function init() {
  wireModal();
  wireRangeButtons();

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
