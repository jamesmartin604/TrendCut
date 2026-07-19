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

import { nearestDefinedIndex, nearestIndex } from "./chart-math.js";
import { getTdeeHistory } from "./api.js";
import { renderTdeeChart } from "./tdee-chart.js";

const $ = (id) => document.getElementById(id);

const state = {
  history: null,
  chartGeometry: null, // hit-test geometry from the last chart render (tooltip)
};

const fmtInt = (v) => Math.round(v).toLocaleString("en-US");

// Same short human date as the weight-chart tooltip (e.g. "Jul 16"), UTC so the
// calendar day never shifts with the viewer's timezone.
const fmtDateHuman = (iso) =>
  new Date(`${iso}T00:00:00Z`).toLocaleString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });

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
  state.chartGeometry = renderTdeeChart($("tdee-chart"), { points: state.history.points });
  hideTooltip(); // a re-render (resize) invalidates any open tooltip
}

// ---------- chart tooltip (hover / tap) ---------- //
// Mirrors the weight chart's tooltip (app.js): map the cursor back to the nearest
// plotted point via the shared `nearestIndex`, skip gaps with `nearestDefinedIndex`,
// and show date + TDEE value only. Same show/hide/positioning + tap-to-dismiss.

function hideTooltip() {
  $("tdee-chart-tooltip").hidden = true;
  $("tdee-chart-cursor").hidden = true;
}

function updateTooltip(clientX) {
  const geom = state.chartGeometry;
  const el = $("tdee-chart");
  if (!geom) return hideTooltip();
  const rect = el.getBoundingClientRect();
  if (rect.width === 0) return hideTooltip();

  // Cursor → viewBox space (== chart pixel space at render time).
  const vbX = (clientX - rect.left) * (geom.width / rect.width);
  let idx = nearestIndex(vbX, geom.x0, geom.x1, geom.points.length);
  if (idx < 0) return hideTooltip();
  // Snap off gaps (insufficient_data days carry no estimate) to the nearest point
  // that actually has a TDEE to show.
  if (geom.points[idx].tdee == null) {
    idx = nearestDefinedIndex(
      geom.points.map((p) => p.tdee),
      idx,
    );
    if (idx < 0) return hideTooltip();
  }
  const p = geom.points[idx];

  // Only show when the cursor is genuinely near the point's column.
  if (Math.abs(p.xPos - vbX) > 30) return hideTooltip();

  const tip = $("tdee-chart-tooltip");
  tip.innerHTML =
    `<div class="tt-date">${fmtDateHuman(p.date)}</div>` +
    `<div class="tt-row"><span class="tt-k">TDEE</span><span class="tt-v">${fmtInt(p.tdee)} kcal</span></div>`;
  tip.hidden = false;

  const cursor = $("tdee-chart-cursor");
  cursor.style.left = `${p.xPos}px`;
  cursor.style.top = `${p.yPos}px`;
  cursor.hidden = false;

  // Center above the point; flip below near the top edge; clamp within the chart.
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  let left = p.xPos - tw / 2;
  left = Math.max(4, Math.min(left, geom.width - tw - 4));
  let top = p.yPos - th - 12;
  if (top < 4) top = p.yPos + 14;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function wireTooltip() {
  const el = $("tdee-chart");
  const move = (e) => updateTooltip(e.clientX);
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerdown", move); // touch tap / mouse click
  el.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") hideTooltip();
  });
  // Tapping/clicking outside the chart dismisses the tooltip (touch).
  document.addEventListener("pointerdown", (e) => {
    if (!el.contains(e.target)) hideTooltip();
  });
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
  wireTooltip();

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
