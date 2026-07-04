/**
 * Weight trend chart — hand-rolled SVG (no charting library, PRD FR-22).
 *
 * Draw-only: all coordinate math lives in the pure functions of chart-math.js.
 * This module measures the container, asks the math for pixel positions, and
 * assembles SVG markup: faint raw-weight dots, one bold EWMA trend line, faint
 * axes, weekly vertical guides, and a dashed target line.
 */

import {
  extent,
  formatDayLabel,
  linearScale,
  linePath,
  padDomain,
  round1,
  ticks,
  weekStartIndices,
  xLabelIndices,
} from "./chart-math.js";

const MARGIN = { top: 12, right: 16, bottom: 28, left: 44 };

const COLOR = {
  trend: "#0058be", // bold EWMA line (token: primary)
  raw: "#3b82f6", // faint raw points (DESIGN.md signal blue)
  grid: "#f3f3f3",
  gridBase: "#e5e5e5",
  target: "#727785",
};

/**
 * Render the chart into `container`.
 *
 * @param {HTMLElement} container sized box the SVG fills
 * @param {{points: {date: string, weight_kg: number|null, trend_kg: number|null}[],
 *          targetWeight: number|null, unitLabel?: string}} data trend series (values
 *          already in the display unit) + target + the unit label for annotations
 */
export function renderWeightChart(container, { points, targetWeight, unitLabel = "kg" }) {
  const hasWeight = points.some((p) => p.weight_kg != null || p.trend_kg != null);
  if (!hasWeight) {
    container.innerHTML =
      '<div class="chart-empty">No weigh-ins yet — log your first entry and the trend will appear here.</div>';
    return null;
  }

  const width = container.clientWidth;
  const height = container.clientHeight;
  if (width === 0 || height === 0) return null;

  const x0 = MARGIN.left;
  const x1 = width - MARGIN.right;
  const y0 = MARGIN.top;
  const y1 = height - MARGIN.bottom;

  // --- scales (pure math) ------------------------------------------------- #
  const values = [];
  for (const p of points) {
    values.push(p.weight_kg, p.trend_kg);
  }
  if (targetWeight != null) values.push(targetWeight);
  const [dLo, dHi] = padDomain(extent(values));
  const xScale = linearScale(0, Math.max(points.length - 1, 1), x0, x1);
  const yScale = linearScale(dLo, dHi, y1, y0); // inverted: bigger kg = higher up

  const dates = points.map((p) => p.date);
  const parts = [];

  // --- horizontal gridlines + y labels ------------------------------------ #
  for (const t of ticks(dLo, dHi, 3)) {
    const y = round1(yScale(t));
    parts.push(
      `<line x1="${x0}" x2="${x1}" y1="${y}" y2="${y}" stroke="${COLOR.grid}" stroke-width="1"/>`,
      `<text class="chart-axis-label" x="${x0 - 8}" y="${y + 4}" text-anchor="end">${round1(t)}</text>`,
    );
  }

  // --- weekly vertical guides (only while they stay sparse) ---------------- #
  const weeks = weekStartIndices(dates);
  if (weeks.length > 0 && weeks.length <= 14) {
    for (const i of weeks) {
      const x = round1(xScale(i));
      parts.push(
        `<line x1="${x}" x2="${x}" y1="${y0}" y2="${y1}" stroke="${COLOR.grid}" stroke-width="1"/>`,
      );
    }
  }

  // --- baseline ------------------------------------------------------------ #
  parts.push(
    `<line x1="${x0}" x2="${x1}" y1="${y1}" y2="${y1}" stroke="${COLOR.gridBase}" stroke-width="1"/>`,
  );

  // --- dashed target line --------------------------------------------------- #
  if (targetWeight != null && targetWeight >= dLo && targetWeight <= dHi) {
    const y = round1(yScale(targetWeight));
    parts.push(
      `<line x1="${x0}" x2="${x1}" y1="${y}" y2="${y}" stroke="${COLOR.target}" ` +
        'stroke-width="1.5" stroke-dasharray="8 4" opacity="0.5"/>',
      `<text class="chart-axis-label" x="${x0 + 4}" y="${y - 6}">${round1(targetWeight)} ${unitLabel} target</text>`,
    );
  }

  // --- raw daily points (faint) --------------------------------------------- #
  const dots = [];
  for (let i = 0; i < points.length; i += 1) {
    const w = points[i].weight_kg;
    if (w == null) continue;
    dots.push(`<circle cx="${round1(xScale(i))}" cy="${round1(yScale(w))}" r="3"/>`);
  }
  parts.push(`<g fill="${COLOR.raw}" opacity="0.4">${dots.join("")}</g>`);

  // --- bold EWMA trend line --------------------------------------------------- #
  const trendPts = points.map((p, i) =>
    p.trend_kg == null ? null : { x: xScale(i), y: yScale(p.trend_kg) },
  );
  parts.push(
    `<path d="${linePath(trendPts)}" fill="none" stroke="${COLOR.trend}" ` +
      'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
  );

  // --- x labels ---------------------------------------------------------------- #
  for (const i of xLabelIndices(points.length, 1)) {
    const anchor = i === 0 ? "start" : i === points.length - 1 ? "end" : "middle";
    parts.push(
      `<text class="chart-axis-label" x="${round1(xScale(i))}" y="${y1 + 20}" ` +
        `text-anchor="${anchor}">${formatDayLabel(dates[i])}</text>`,
    );
  }

  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Weight trend chart">${parts.join("")}</svg>`;

  // Geometry for hit-testing (tooltip). Values are in the SAME display units passed
  // in; positions are in the SVG's viewBox space, which equals the container's
  // pixel space (viewBox is 0 0 width height). The caller uses `nearestIndex` with
  // x0/x1 to invert a cursor x back to a data point.
  return {
    width,
    height,
    x0,
    x1,
    y0,
    y1,
    points: points.map((p, i) => ({
      i,
      date: p.date,
      weight: p.weight_kg,
      trend: p.trend_kg,
      xPos: xScale(i),
      yWeight: p.weight_kg == null ? null : yScale(p.weight_kg),
      yTrend: p.trend_kg == null ? null : yScale(p.trend_kg),
    })),
  };
}
