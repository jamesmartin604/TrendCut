/**
 * TDEE-over-time screen — loads /tdee-history and renders the hand-rolled drift
 * chart plus honest summary stats (baseline, current, net drift).
 *
 * Faithful surfacing (same discipline as the Dashboard): points the estimator
 * declined on are `insufficient_data` with a null TDEE and simply don't appear —
 * the chart breaks its line across them rather than inventing a value. Summary
 * stats are computed only from real (non-null) estimates; if there are none, the
 * screen says so plainly instead of showing fabricated numbers.
 */

import { getTdeeHistory } from "./api.js";
import { renderTdeeChart } from "./tdee-chart.js";

const $ = (id) => document.getElementById(id);

const state = { history: null };

const fmtInt = (v) => Math.round(v).toLocaleString("en-US");

function confidentPoints() {
  return state.history.points.filter((p) => p.tdee != null);
}

function renderSummary() {
  const pts = confidentPoints();
  const currentEl = $("tdee-current");
  const driftChip = $("drift-chip");

  if (pts.length === 0) {
    currentEl.hidden = true;
    driftChip.hidden = true;
    $("baseline-value").textContent = "–";
    $("baseline-unit").hidden = true;
    $("baseline-note").textContent = "No confident estimate yet";
    $("drift-value").textContent = "–";
    $("drift-unit").hidden = true;
    $("drift-note").textContent = "Appears once there's a baseline";
    return;
  }

  const baseline = pts[0];
  const current = pts[pts.length - 1];

  currentEl.hidden = false;
  $("current-value").textContent = fmtInt(current.tdee);

  $("baseline-value").textContent = fmtInt(baseline.tdee);
  $("baseline-unit").hidden = false;
  $("baseline-note").textContent = `First confident estimate · ${baseline.date}`;

  const drift = current.tdee - baseline.tdee;
  const sign = drift > 0 ? "+" : drift < 0 ? "−" : "";
  $("drift-value").textContent = `${sign}${fmtInt(Math.abs(drift))}`;
  $("drift-unit").hidden = false;
  $("drift-note").textContent =
    drift < 0
      ? "Maintenance has drifted down (adaptive thermogenesis)"
      : drift > 0
        ? "Maintenance has drifted up"
        : "No net change since baseline";

  // Drift chip in the header, mirroring the reference's "N-week drift" badge.
  driftChip.hidden = false;
  driftChip.textContent = `Drift ${sign}${fmtInt(Math.abs(drift))} kcal`;
  driftChip.classList.toggle("chip-drift-down", drift < 0);
}

function renderChart() {
  renderTdeeChart($("tdee-chart"), { points: state.history.points });
}

async function init() {
  const chartEl = $("tdee-chart");
  let lastWidth = 0;
  new ResizeObserver((entries) => {
    const width = entries[0].contentRect.width;
    if (state.history && Math.abs(width - lastWidth) > 1) {
      lastWidth = width;
      renderChart();
    }
  }).observe(chartEl);

  try {
    state.history = await getTdeeHistory();
    renderSummary();
    renderChart();
  } catch (err) {
    const el = $("page-error");
    el.textContent = `Couldn't load TDEE history: ${err.message}`;
    el.hidden = false;
  }
}

init();
