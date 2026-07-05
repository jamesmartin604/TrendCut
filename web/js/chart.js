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
  goalWeightAt,
  indexDomain,
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
  goal: "#006c49", // plan/goal line (token: secondary green) — a quiet reference
};

/**
 * Render the chart into `container`.
 *
 * @param {HTMLElement} container sized box the SVG fills
 * @param {{points: {date: string, weight_kg: number|null, trend_kg: number|null}[],
 *          targetWeight: number|null, unitLabel?: string,
 *          goal?: {startX: number, startWeight: number, endX: number, endWeight: number}|null,
 *          goalTargetIso?: string|null}} data trend series (values already in the
 *          display unit) + the horizontal target + the optional goal-line anchors
 *          (day-index axis, display units) and the target-date label for its x-tick
 */
export function renderWeightChart(
  container,
  { points, targetWeight, unitLabel = "kg", goal = null, goalTargetIso = null },
) {
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
  // x-domain frames the data points, but stretches to the target date when the goal
  // line reaches into the future (indexDomain); hiding the line reframes to the data.
  const [xDomLo, xDomHi] = indexDomain(points.length, goal);
  // The goal line's visible span on the extended axis — its endpoints join the
  // y-domain so the plan can't run off the top/bottom of the plot.
  const goalXa = goal ? Math.max(goal.startX, xDomLo) : 0;
  const goalXb = goal ? Math.min(goal.endX, xDomHi) : 0;
  const goalVisible = goal != null && goal.endX !== goal.startX && goalXb >= goalXa;

  const values = [];
  for (const p of points) {
    values.push(p.weight_kg, p.trend_kg);
  }
  if (targetWeight != null) values.push(targetWeight);
  if (goalVisible) {
    values.push(goalWeightAt(goal, goalXa), goalWeightAt(goal, goalXb));
  }
  const [dLo, dHi] = padDomain(extent(values));
  const xScale = linearScale(xDomLo, xDomHi, x0, x1);
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

  // --- goal (plan) line: trend-start → target, into the future ------------- #
  // A quiet DOTTED green diagonal, deliberately unlike the solid blue trend and the
  // grey dashed horizontal target it lands on — it reads as a plan/reference, not a
  // prediction. Clipped to the visible span; it can enter from the left edge and
  // runs out to the target date on the right.
  if (goalVisible) {
    const gx1 = round1(xScale(goalXa));
    const gy1 = round1(yScale(goalWeightAt(goal, goalXa)));
    const gx2 = round1(xScale(goalXb));
    const gy2 = round1(yScale(goalWeightAt(goal, goalXb)));
    parts.push(
      `<line x1="${gx1}" y1="${gy1}" x2="${gx2}" y2="${gy2}" stroke="${COLOR.goal}" ` +
        'stroke-width="2" stroke-dasharray="1 6" stroke-linecap="round" opacity="0.8"/>',
      `<text class="chart-axis-label" x="${gx2}" y="${gy2 - 8}" text-anchor="end" fill="${COLOR.goal}">goal</text>`,
    );
    // Label the target date on the x-axis when the plan extends past the data.
    if (goalTargetIso != null && goal.endX > points.length - 1) {
      parts.push(
        `<text class="chart-axis-label" x="${gx2}" y="${y1 + 20}" text-anchor="end">${formatDayLabel(goalTargetIso)}</text>`,
      );
    }
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
    // x0/x1 are the pixel columns of the FIRST and LAST data points (what the
    // tooltip's nearestIndex inverts against). These equal the plot edges in the
    // normal case, but when the goal line stretches the domain into the future the
    // last point sits left of the right edge — so derive them from the scale, not
    // the margins, or hover mapping would drift.
    x0: xScale(0),
    x1: xScale(Math.max(points.length - 1, 0)),
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
