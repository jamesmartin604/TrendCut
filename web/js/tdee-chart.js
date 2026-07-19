/**
 * TDEE-over-time chart — hand-rolled SVG (no charting library, PRD FR-22).
 *
 * Draw-only: all coordinate math is the shared pure functions in chart-math.js
 * (the same ones the weight chart uses). This module turns the /tdee-history
 * series into SVG: a faint confidence band (which naturally widens on
 * low-confidence days), a bold estimate line, and points — while honestly breaking
 * the line across `insufficient_data` gaps rather than drawing through them.
 */

import {
  definedRange,
  extent,
  formatDayLabel,
  linearScale,
  linePath,
  padDomain,
  round1,
  ticks,
  xLabelIndices,
} from "./chart-math.js";

const MARGIN = { top: 12, right: 16, bottom: 28, left: 52 };

const COLOR = {
  line: "#0058be",
  lowConf: "#f59e0b", // amber points where the estimate is low-confidence
  band: "rgba(59, 130, 246, 0.10)",
  grid: "#f3f3f3",
  gridBase: "#e5e5e5",
};

/**
 * Split points into runs of consecutive days that HAVE an estimate (tdee != null).
 * Each gap (insufficient_data) ends a run, so the line/band never bridge a gap.
 * Indices are kept so x-positions stay on the full-range day axis.
 */
function estimateRuns(points) {
  const runs = [];
  let current = null;
  points.forEach((p, i) => {
    if (p.tdee == null) {
      current = null;
      return;
    }
    if (current === null) {
      current = [];
      runs.push(current);
    }
    current.push({ ...p, i });
  });
  return runs;
}

/**
 * Render the TDEE-history chart.
 *
 * @param {HTMLElement} container sized box the SVG fills
 * @param {{points: {date: string, status: string, tdee: number|null,
 *          ci_low: number|null, ci_high: number|null}[]}} data the /tdee-history series
 */
export function renderTdeeChart(container, { points }) {
  // Frame the x-axis to the ESTIMABLE range — the first through last day that has a
  // real estimate — not the full logging period. The pre-window warm-up days have
  // no TDEE and must not stretch the axis (otherwise the line is crushed to the
  // right edge). If nothing is estimable yet, show the honest empty state.
  const xRange = definedRange(points.map((p) => p.tdee));
  if (xRange === null) {
    container.innerHTML =
      '<div class="chart-empty">Not enough data yet to chart TDEE over time — keep logging and the estimate history will build up here.</div>';
    return null;
  }
  const [iFirst, iLast] = xRange;
  const runs = estimateRuns(points);

  const width = container.clientWidth;
  const height = container.clientHeight;
  if (width === 0 || height === 0) return null;

  const x0 = MARGIN.left;
  const x1 = width - MARGIN.right;
  const y0 = MARGIN.top;
  const y1 = height - MARGIN.bottom;

  // y-domain fits the plotted estimate values + their confidence bounds; x-domain
  // clips to [iFirst, iLast] with a small symmetric pad (~a few % of the span, at
  // least half a day) so the end points don't sit on the axis edges.
  const bounds = [];
  for (const p of points) {
    if (p.tdee != null) bounds.push(p.ci_low, p.ci_high, p.tdee);
  }
  const [dLo, dHi] = padDomain(extent(bounds), 0.1, 50);
  const [xLo, xHi] = padDomain([iFirst, iLast], 0.03, 0.5);
  const xScale = linearScale(xLo, xHi, x0, x1);
  const yScale = linearScale(dLo, dHi, y1, y0); // inverted: more kcal = higher up

  const dates = points.map((p) => p.date);
  const parts = [];

  // Horizontal gridlines + y labels (kcal).
  for (const t of ticks(dLo, dHi, 3)) {
    const y = round1(yScale(t));
    parts.push(
      `<line x1="${x0}" x2="${x1}" y1="${y}" y2="${y}" stroke="${COLOR.grid}" stroke-width="1"/>`,
      `<text class="chart-axis-label" x="${x0 - 8}" y="${y + 4}" text-anchor="end">${Math.round(t)}</text>`,
    );
  }
  parts.push(
    `<line x1="${x0}" x2="${x1}" y1="${y1}" y2="${y1}" stroke="${COLOR.gridBase}" stroke-width="1"/>`,
  );

  // Per run: confidence band (upper along ci_high, back along ci_low), then line.
  for (const run of runs) {
    if (run.length >= 2) {
      const upper = run.map((p) => `${round1(xScale(p.i))},${round1(yScale(p.ci_high))}`);
      const lower = run
        .slice()
        .reverse()
        .map((p) => `${round1(xScale(p.i))},${round1(yScale(p.ci_low))}`);
      parts.push(
        `<path d="M${upper.join("L")}L${lower.join("L")}Z" fill="${COLOR.band}" stroke="none"/>`,
      );
    }
    const linePts = run.map((p) => ({ x: xScale(p.i), y: yScale(p.tdee) }));
    parts.push(
      `<path d="${linePath(linePts)}" fill="none" stroke="${COLOR.line}" ` +
        'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
    );
  }

  // Points: amber + hollow where low-confidence, solid blue where ok.
  for (const run of runs) {
    for (const p of run) {
      const cx = round1(xScale(p.i));
      const cy = round1(yScale(p.tdee));
      if (p.status === "low_confidence") {
        parts.push(
          `<circle cx="${cx}" cy="${cy}" r="3" fill="var(--surface)" stroke="${COLOR.lowConf}" stroke-width="1.5"/>`,
        );
      }
    }
  }
  // Emphasise the latest estimate with a filled marker.
  const lastRun = runs[runs.length - 1];
  const lastPt = lastRun[lastRun.length - 1];
  parts.push(
    `<circle cx="${round1(xScale(lastPt.i))}" cy="${round1(yScale(lastPt.tdee))}" r="4" fill="${COLOR.line}"/>`,
  );

  // X labels — spaced across the FRAMED range [iFirst, iLast], not the full series.
  const subLen = iLast - iFirst + 1;
  for (const rel of xLabelIndices(subLen, 2)) {
    const i = iFirst + rel;
    const anchor = rel === 0 ? "start" : rel === subLen - 1 ? "end" : "middle";
    parts.push(
      `<text class="chart-axis-label" x="${round1(xScale(i))}" y="${y1 + 20}" ` +
        `text-anchor="${anchor}">${formatDayLabel(dates[i])}</text>`,
    );
  }

  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="TDEE over time chart">${parts.join("")}</svg>`;

  // Geometry for hit-testing (tooltip), mirroring the weight chart. Positions are in
  // viewBox space (== container pixel space). x0/x1 are the pixel columns of index 0
  // and the last index — derived from the scale so `nearestIndex` inverts a cursor x
  // back to a data index correctly even though the domain is framed to [iFirst,iLast]
  // (leading warm-up points map off the left edge; their yPos is null so no tooltip).
  return {
    width,
    height,
    x0: xScale(0),
    x1: xScale(Math.max(points.length - 1, 0)),
    y0,
    y1,
    points: points.map((p, i) => ({
      i,
      date: p.date,
      tdee: p.tdee,
      xPos: xScale(i),
      yPos: p.tdee == null ? null : yScale(p.tdee),
    })),
  };
}
