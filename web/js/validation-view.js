/**
 * Pure view logic for the public validation page — no DOM, so it's unit-testable
 * under `node --test`. Formats the artifact and computes the recovered-vs-true
 * scatter geometry (hand-rolled SVG, no charting library).
 */

/** 0.933 → "93.3%". */
export function fmtPct(rate) {
  return `${Math.round(rate * 1000) / 10}%`;
}

/** Flatten every pass/fail scenario's cases, tagging each with its scenario id. */
export function allCases(report) {
  const out = [];
  for (const s of report.scenarios || []) {
    for (const c of s.cases || []) out.push({ ...c, scenario: s.id });
  }
  return out;
}

/** Shared value→domain for the scatter: spans both true and recovered TDEE. */
export function domainFor(cases) {
  const vals = [];
  for (const c of cases) {
    vals.push(c.true_tdee);
    if (c.recovered_tdee != null) vals.push(c.recovered_tdee);
  }
  if (vals.length === 0) return { min: 0, max: 1 };
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo) * 0.05 || 100;
  return { min: lo - pad, max: hi + pad };
}

function _scales({ width, height, pad, domain }) {
  const span = domain.max - domain.min || 1;
  return {
    x: (v) => pad + ((v - domain.min) / span) * (width - 2 * pad),
    // Invert Y so larger values sit higher.
    y: (v) => height - pad - ((v - domain.min) / span) * (height - 2 * pad),
  };
}

/**
 * Pixel positions for each recovered point (recovered points only — cases the
 * estimator declined are skipped). `within` drives the point's colour.
 */
export function scatterPoints(cases, geom) {
  const { x, y } = _scales(geom);
  const pts = [];
  for (const c of cases) {
    if (c.recovered_tdee == null) continue;
    pts.push({
      cx: x(c.true_tdee),
      cy: y(c.recovered_tdee),
      within: !!c.within_tolerance,
      trueTdee: c.true_tdee,
      recovered: c.recovered_tdee,
    });
  }
  return pts;
}

/** The y = x "perfect recovery" reference line, in pixel space. */
export function diagonal(geom) {
  const { x, y } = _scales(geom);
  return {
    x1: x(geom.domain.min),
    y1: y(geom.domain.min),
    x2: x(geom.domain.max),
    y2: y(geom.domain.max),
  };
}
