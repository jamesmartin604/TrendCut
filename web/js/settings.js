/**
 * Settings screen — loads the current settings, lets the user edit the target,
 * smoothing (α), regression window, and display units, and persists them via
 * PUT /settings. On save the values are written straight to the same settings the
 * Dashboard reads, so returning to the Dashboard reflects them (the pace/projection
 * and the estimator's window/α are all driven by these).
 *
 * Validation is soft: implausible values raise an inline warning but never block a
 * save (same discipline as the log-entry guards).
 */

import { getSettings, updateSettings } from "./api.js";
import { formatWeight, toKg, unitLabel } from "./units.js";

const $ = (id) => document.getElementById(id);

const state = {
  units: "kg",
  targetKg: null, // canonical target weight in kg (display is derived from this)
  window: 28,
};

// ---------- load ---------- //

async function load() {
  const s = await getSettings();
  state.units = s.units === "lb" ? "lb" : "kg";
  state.targetKg = s.target_weight;
  state.window = s.window_days;

  setUnitsButtons(state.units);
  $("target-weight").value = s.target_weight == null ? "" : formatWeight(s.target_weight, state.units);
  $("target-weight-unit").textContent = unitLabel(state.units);
  $("target-date").value = s.target_date ?? "";

  $("alpha-slider").value = s.alpha;
  renderAlpha(s.alpha);
  setWindowButtons(s.window_days);
}

// ---------- units ---------- //

function setUnitsButtons(units) {
  for (const b of document.querySelectorAll("[data-units]")) {
    b.classList.toggle("is-active", b.dataset.units === units);
  }
}

function onUnitsChange(newUnits) {
  if (newUnits === state.units) return;
  // Reconvert whatever's in the field (via kg) so the displayed number is correct
  // in the new unit; keep the canonical kg value stable.
  const raw = $("target-weight").value.trim();
  const kg = raw === "" ? null : toKg(Number(raw), state.units);
  state.units = newUnits;
  setUnitsButtons(newUnits);
  $("target-weight-unit").textContent = unitLabel(newUnits);
  $("target-weight").value = kg == null ? "" : formatWeight(kg, newUnits);
}

// ---------- alpha ---------- //

function renderAlpha(alpha) {
  $("alpha-readout").textContent = Number(alpha).toFixed(2);
  // ~1/α rule-of-thumb window the PRD/UI copy uses.
  $("alpha-window").textContent = `≈${Math.round(1 / alpha)}-day window`;
}

// ---------- window ---------- //

function setWindowButtons(win) {
  state.window = win;
  for (const b of document.querySelectorAll("[data-window]")) {
    b.classList.toggle("is-active", Number(b.dataset.window) === Number(win));
  }
}

// ---------- soft validation ---------- //

function softWarnings() {
  const warnings = [];
  const raw = $("target-weight").value.trim();
  if (raw !== "") {
    const kg = toKg(Number(raw), state.units);
    if (Number.isNaN(kg)) {
      warnings.push("Target weight isn't a number — it won't be saved.");
    } else if (kg < 20 || kg > 400) {
      warnings.push(`Target weight looks off (${raw} ${unitLabel(state.units)}) — saved anyway; check for a typo.`);
    }
  }
  const date = $("target-date").value;
  if (date && date < todayIso()) {
    warnings.push("Target date is in the past — saved anyway.");
  }
  return warnings;
}

function todayIso() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

// ---------- save ---------- //

async function save() {
  const errorEl = $("settings-error");
  const warnEl = $("settings-warnings");
  const savedEl = $("settings-saved");
  errorEl.hidden = true;
  savedEl.hidden = true;

  // Build the patch. α/window/units always go; target fields only when present
  // (the settings endpoint ignores nulls, so an empty field leaves them unchanged).
  const patch = {
    alpha: Number($("alpha-slider").value),
    window_days: Number(state.window),
    units: state.units,
  };
  const rawWeight = $("target-weight").value.trim();
  if (rawWeight !== "" && !Number.isNaN(Number(rawWeight))) {
    patch.target_weight = toKg(Number(rawWeight), state.units);
  }
  const date = $("target-date").value;
  if (date !== "") patch.target_date = date;

  const warnings = softWarnings();
  warnEl.hidden = warnings.length === 0;
  warnEl.innerHTML = warnings.map((w) => `<span>${escapeHtml(w)}</span>`).join("");

  const btn = $("settings-save-btn");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const saved = await updateSettings(patch);
    state.targetKg = saved.target_weight;
    savedEl.hidden = false;
    setTimeout(() => (savedEl.hidden = true), 2500);
  } catch (err) {
    errorEl.textContent = `Couldn't save: ${err.message}`;
    errorEl.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Save settings";
  }
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------- export / backup ---------- //

/**
 * Download all entries via the existing GET /entries/export endpoint as a dated
 * file. Fetches first (rather than a plain link) so a failure surfaces an inline
 * message instead of navigating the browser to an error page.
 */
async function exportData(format) {
  const errEl = $("export-error");
  errEl.hidden = true;
  try {
    const res = await fetch(`/entries/export?format=${format}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tdee-backup-${todayIso()}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    errEl.textContent = `Export failed: ${err.message}. Try again.`;
    errEl.hidden = false;
  }
}

// ---------- import ---------- //

// File text held between "picked" and "confirmed" — the POST happens only on the
// explicit confirm click, never on file selection (import upserts: an imported
// date overwrites the existing entry for that date, and there is no undo).
let pendingImport = null;

function resetImportUi() {
  pendingImport = null;
  $("import-confirm").hidden = true;
  $("import-file").value = ""; // re-selecting the same file must re-fire `change`
}

async function onImportFilePicked(file) {
  $("import-summary").hidden = true;
  $("import-warnings").hidden = true;
  $("import-error").hidden = true;
  if (!file) return;
  try {
    pendingImport = { name: file.name, text: await file.text() };
  } catch (err) {
    $("import-error").textContent = `Couldn't read the file: ${err.message}`;
    $("import-error").hidden = false;
    return;
  }
  $("import-confirm-text").textContent =
    `Import "${file.name}"? Rows are matched by date: dates you've already logged ` +
    "will be OVERWRITTEN by the file's values, and there is no undo. New dates are added.";
  $("import-confirm").hidden = false;
}

async function runImport() {
  const { text } = pendingImport;
  const btn = $("import-confirm-btn");
  btn.disabled = true;
  btn.textContent = "Importing…";
  try {
    const res = await fetch("/entries/import", {
      method: "POST",
      headers: { "Content-Type": "text/csv" },
      body: text,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    renderImportResult(await res.json());
  } catch (err) {
    $("import-error").textContent = `Import failed: ${err.message}. Nothing may have been saved — check History.`;
    $("import-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Import and overwrite";
    resetImportUi();
  }
}

/**
 * Show the endpoint's ImportResult inline: the imported count as the headline, and
 * every warning (skipped rows + why, unit assumption, overwrite count) verbatim —
 * the warnings are the honest record of what the tolerant parser did.
 */
function renderImportResult(result) {
  const summary = $("import-summary");
  const warnEl = $("import-warnings");
  if (result.imported === 0) {
    $("import-error").textContent = "Nothing was imported.";
    $("import-error").hidden = false;
  } else {
    summary.textContent =
      `Imported ${result.imported} ${result.imported === 1 ? "entry" : "entries"}. ` +
      "The Dashboard and History recompute from the new data on their next load.";
    summary.hidden = false;
  }
  if (result.warnings.length > 0) {
    warnEl.innerHTML = result.warnings.map((w) => `<span>${escapeHtml(w)}</span>`).join("");
    warnEl.hidden = false;
  }
}

// ---------- wiring ---------- //

function init() {
  for (const b of document.querySelectorAll("[data-units]")) {
    b.addEventListener("click", () => onUnitsChange(b.dataset.units));
  }
  for (const b of document.querySelectorAll("[data-window]")) {
    b.addEventListener("click", () => setWindowButtons(Number(b.dataset.window)));
  }
  $("alpha-slider").addEventListener("input", (e) => renderAlpha(Number(e.target.value)));
  $("settings-form").addEventListener("submit", (e) => {
    e.preventDefault();
    save();
  });
  $("export-csv").addEventListener("click", () => exportData("csv"));
  $("export-json").addEventListener("click", () => exportData("json"));

  $("import-csv").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", (e) => onImportFilePicked(e.target.files[0]));
  $("import-confirm-btn").addEventListener("click", runImport);
  $("import-cancel-btn").addEventListener("click", resetImportUi);

  load().catch((err) => {
    const el = $("page-error");
    el.textContent = `Couldn't load settings: ${err.message}`;
    el.hidden = false;
  });
}

init();
