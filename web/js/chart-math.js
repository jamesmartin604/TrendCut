/**
 * Pure chart math — data-value → pixel mapping, tick generation, path building.
 *
 * No DOM, no fetch, no side effects: every function here maps plain inputs to
 * plain outputs, so the scaling logic is isolatable and easy to reason about
 * (the same purity discipline as the Python estimator). The draw code in
 * chart.js consumes these; it contains no coordinate arithmetic of its own.
 */

/**
 * Min/max of a list of numbers, ignoring null/undefined/NaN.
 * Returns null when no finite values exist.
 */
export function extent(values) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v == null || Number.isNaN(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo <= hi ? [lo, hi] : null;
}

/**
 * Pad a [lo, hi] domain outward by `frac` of its span on each side, so points
 * never sit on the chart edge. A degenerate (zero-span) domain gets an absolute
 * ±`minPad` so it still renders.
 */
export function padDomain([lo, hi], frac = 0.1, minPad = 0.5) {
  const span = hi - lo;
  const pad = span > 0 ? span * frac : minPad;
  return [lo - pad, hi + pad];
}

/**
 * Linear scale factory: maps domain [d0, d1] onto range [r0, r1].
 * Returns a pure mapping function value → pixel.
 */
export function linearScale(d0, d1, r0, r1) {
  const span = d1 - d0;
  // Degenerate domain: park everything mid-range rather than dividing by zero.
  if (span === 0) return () => (r0 + r1) / 2;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/**
 * "Nice" tick values covering [lo, hi]: picks a 1/2/5×10^k step so that at most
 * `count`+1 ticks land inside the domain, aligned to round numbers.
 */
export function ticks(lo, hi, count = 3) {
  const span = hi - lo;
  if (!(span > 0) || count < 1) return [];
  const rawStep = span / count;
  const mag = 10 ** Math.floor(Math.log10(rawStep));
  let step = mag;
  for (const m of [1, 2, 5, 10]) {
    step = m * mag;
    if (step >= rawStep) break;
  }
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) {
    out.push(Math.round(t / step) * step);
  }
  return out;
}

/**
 * SVG path string for a polyline through `pts` ([{x, y}] with null entries for
 * gaps). Null entries break the line into separate segments — a gap in the data
 * stays a visible gap, it is not bridged.
 */
export function linePath(pts) {
  let d = "";
  let pen = false;
  for (const p of pts) {
    if (p == null) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${round1(p.x)},${round1(p.y)}`;
    pen = true;
  }
  return d;
}

/** Round to 1 decimal for compact SVG output. */
export function round1(v) {
  return Math.round(v * 10) / 10;
}

/**
 * Indices of x-axis label positions: first, last, and up to `inner` evenly
 * spaced in between (deduplicated, so tiny series get fewer labels).
 */
export function xLabelIndices(n, inner = 1) {
  if (n <= 0) return [];
  if (n === 1) return [0];
  const idx = new Set([0, n - 1]);
  for (let k = 1; k <= inner; k += 1) {
    idx.add(Math.round((k * (n - 1)) / (inner + 1)));
  }
  return [...idx].sort((a, b) => a - b);
}

/**
 * Indices where a new ISO week starts (Mondays), for faint vertical guides.
 * `isoDates` is an array of "YYYY-MM-DD" strings.
 */
export function weekStartIndices(isoDates) {
  const out = [];
  for (let i = 0; i < isoDates.length; i += 1) {
    // Date.getUTCDay(): 0 = Sunday … 1 = Monday.
    if (new Date(`${isoDates[i]}T00:00:00Z`).getUTCDay() === 1) out.push(i);
  }
  return out;
}

/** "2026-06-01" → "JUN 1" (short, uppercase axis label). */
export function formatDayLabel(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const month = d.toLocaleString("en", { month: "short", timeZone: "UTC" });
  return `${month} ${d.getUTCDate()}`.toUpperCase();
}
