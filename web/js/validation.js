/**
 * Public validation page — reads the pre-generated /validation.json (a static asset,
 * no auth) and renders the estimator's real accuracy: headline stat, a hand-rolled
 * recovered-vs-true scatter, the full scenario matrix (weak spots included), the
 * anti-circularity demonstration, and the honest limitations.
 *
 * Deliberately does NOT import api.js — this page touches no authenticated endpoint,
 * so it never triggers the login redirect. It reads only synthetic results.
 */

import { allCases, diagonal, domainFor, fmtPct, scatterPoints } from "./validation-view.js";

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function el(tag, attrs = {}) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function renderScatter(report) {
  const svg = $("scatter");
  const width = 640;
  const height = 360;
  const pad = 44;
  const cases = allCases(report);
  const domain = domainFor(cases);
  const geom = { width, height, pad, domain };

  // Perfect-recovery reference line.
  const d = diagonal(geom);
  svg.appendChild(
    el("line", { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2, class: "scatter-diagonal" }),
  );

  // Axis labels.
  const xlabel = el("text", { x: width / 2, y: height - 8, class: "scatter-axis" });
  xlabel.textContent = "True TDEE (kcal/day)";
  svg.appendChild(xlabel);
  const ylabel = el("text", {
    x: 14,
    y: height / 2,
    class: "scatter-axis",
    transform: `rotate(-90 14 ${height / 2})`,
  });
  ylabel.textContent = "Recovered TDEE";
  svg.appendChild(ylabel);

  for (const p of scatterPoints(cases, geom)) {
    const dot = el("circle", {
      cx: p.cx.toFixed(1),
      cy: p.cy.toFixed(1),
      r: 4,
      class: p.within ? "scatter-dot is-pass" : "scatter-dot is-fail",
    });
    const title = el("title");
    title.textContent = `true ${p.trueTdee}, recovered ${p.recovered} kcal`;
    dot.appendChild(title);
    svg.appendChild(dot);
  }
}

function renderScenarios(report) {
  const host = $("scenarios");
  host.innerHTML = "";
  for (const s of report.scenarios) {
    const weak = s.pass_rate < 1;
    const card = document.createElement("div");
    card.className = `validation-scenario${weak ? " is-weak" : ""}`;
    const failed = s.n - s.n_passed;
    card.innerHTML =
      `<div class="validation-scenario-head">` +
      `<h3 class="validation-scenario-title">${esc(s.label)}</h3>` +
      `<span class="validation-scenario-rate${weak ? " is-weak" : ""}">${fmtPct(s.pass_rate)}</span>` +
      `</div>` +
      `<p class="validation-scenario-desc">${esc(s.description)}</p>` +
      `<dl class="validation-stats">` +
      `<div><dt>Tolerance</dt><dd>±${s.tolerance_kcal} kcal</dd></div>` +
      `<div><dt>Within tolerance</dt><dd>${s.n_passed} / ${s.n}` +
      `${failed ? ` <span class="validation-fail-count">(${failed} miss)</span>` : ""}</dd></div>` +
      `<div><dt>Mean error</dt><dd>${s.mean_abs_error ?? "–"} kcal</dd></div>` +
      `<div><dt>Worst error</dt><dd>${s.max_abs_error ?? "–"} kcal</dd></div>` +
      `</dl>`;
    host.appendChild(card);
  }
}

function renderMismatch(report) {
  const m = report.density_mismatch;
  $("mismatch-desc").textContent = m.description;
  const rows = m.groups
    .map(
      (g) =>
        `<tr><td>${g.true_energy_density} kcal/kg</td>` +
        `<td>${g.assumed_energy_density} kcal/kg</td>` +
        `<td>${g.expected_bias_kcal >= 0 ? "+" : ""}${g.expected_bias_kcal} kcal</td>` +
        `<td>${g.observed_mean_bias_kcal >= 0 ? "+" : ""}${g.observed_mean_bias_kcal} kcal</td></tr>`,
    )
    .join("");
  $("mismatch-table").innerHTML =
    `<thead><tr><th>Simulator truth</th><th>Estimator assumes</th>` +
    `<th>Predicted bias</th><th>Observed bias</th></tr></thead><tbody>${rows}</tbody>`;
}

function render(report) {
  const meta = report.meta;
  $("overall-rate").textContent = fmtPct(meta.overall_pass_rate);
  $("overall-sub").textContent =
    `${meta.total_passed} of ${meta.total_cases} synthetic cases across all scenarios ` +
    `(${meta.seeds_per_cell} seeds per condition).`;
  $("repro-note").textContent = meta.reproducibility;
  renderScatter(report);
  renderScenarios(report);
  renderMismatch(report);
}

async function init() {
  try {
    const res = await fetch("/validation.json", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    render(await res.json());
  } catch (err) {
    const e = $("page-error");
    e.textContent = `Couldn't load the validation results: ${err.message}`;
    e.hidden = false;
  }
}

init();
