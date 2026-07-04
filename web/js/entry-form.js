/**
 * Shared entry form — the single Log/Edit modal used by BOTH the Dashboard ("Log
 * entry", new day, date editable) and the History screen ("edit", fixed date).
 *
 * It injects its own modal markup once (reusing the app's existing modal CSS) and
 * exposes `openEntryForm`. All the entry-writing logic lives here in one place:
 * unit conversion (display ↔ kg), weight-only / calorie-only days, and surfacing
 * the API's soft input-guard warnings inline without blocking the save.
 */

import { getEntry, putEntry } from "./api.js";
import { formatWeight, toKg, unitLabel } from "./units.js";

const $ = (id) => document.getElementById(id);

const MARKUP = `
<div class="modal-overlay" id="ef-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="ef-title">
    <div class="modal-header">
      <div>
        <h2 class="modal-title" id="ef-title">New Entry</h2>
        <p class="modal-subtitle" id="ef-subtitle">Weight-only or calorie-only days are fine.</p>
      </div>
      <button class="modal-close" id="ef-close" type="button" aria-label="Close">&times;</button>
    </div>
    <form class="modal-form" id="ef-form" novalidate>
      <div class="field">
        <label class="field-label" for="ef-date">Date</label>
        <div class="field-box">
          <input class="field-input" id="ef-date" name="date" type="date" required />
        </div>
      </div>
      <div class="field-grid">
        <div class="field">
          <label class="field-label" for="ef-weight">Weight</label>
          <div class="field-box">
            <input class="field-input field-input-big" id="ef-weight" name="weight" type="number"
              inputmode="decimal" step="0.1" placeholder="00.0" />
            <span class="unit-mono" id="ef-weight-unit">kg</span>
          </div>
        </div>
        <div class="field">
          <label class="field-label" for="ef-kcal">Calories</label>
          <div class="field-box">
            <input class="field-input field-input-big" id="ef-kcal" name="kcal" type="number"
              inputmode="numeric" step="1" placeholder="0000" />
            <span class="unit-mono">kcal</span>
          </div>
        </div>
      </div>
      <div class="form-warnings" id="ef-warnings" hidden></div>
      <div class="form-error" id="ef-error" hidden></div>
      <div class="modal-actions">
        <button class="btn btn-primary btn-wide" id="ef-save" type="submit">Save entry</button>
        <button class="btn btn-ghost btn-wide" id="ef-cancel" type="button">Cancel</button>
      </div>
    </form>
  </div>
</div>`;

let els = null;
const ctx = { units: "kg", dateEditable: true, onSaved: null };

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function close() {
  els.overlay.hidden = true;
}

/** Pull any existing entry for the date so editing/re-logging shows current values. */
async function prefill(date) {
  try {
    const entry = await getEntry(date);
    if (els.date.value !== date) return; // date changed mid-fetch (new-entry flow)
    els.weight.value = entry?.weight_kg == null ? "" : formatWeight(entry.weight_kg, ctx.units);
    els.kcal.value = entry?.kcal ?? "";
  } catch {
    /* prefill is best-effort; saving still works */
  }
}

async function save() {
  const date = els.date.value;
  const weightRaw = els.weight.value.trim();
  const kcalRaw = els.kcal.value.trim();
  els.error.hidden = true;
  els.warnings.hidden = true;

  if (!date) {
    els.error.textContent = "Pick a date.";
    els.error.hidden = false;
    return;
  }
  if (weightRaw === "" && kcalRaw === "") {
    els.error.textContent = "Enter a weight, calories, or both.";
    els.error.hidden = false;
    return;
  }

  els.save.disabled = true;
  els.save.textContent = "Saving…";
  try {
    const saved = await putEntry(date, {
      weight_kg: weightRaw === "" ? null : toKg(Number(weightRaw), ctx.units),
      kcal: kcalRaw === "" ? null : Math.round(Number(kcalRaw)),
    });
    // Recompute downstream on the calling page (Dashboard/History re-fetch).
    if (ctx.onSaved) await ctx.onSaved();
    if (saved.warnings.length > 0) {
      // Soft input guards: the entry IS saved — surface, don't block.
      els.warnings.innerHTML = saved.warnings
        .map((w) => `<span>Saved with a warning: ${escapeHtml(w)}</span>`)
        .join("");
      els.warnings.hidden = false;
      els.save.disabled = false;
      els.save.textContent = "Save entry";
    } else {
      close();
    }
  } catch (err) {
    els.error.textContent = `Couldn't save: ${err.message}`;
    els.error.hidden = false;
    els.save.disabled = false;
    els.save.textContent = "Save entry";
  }
}

function ensure() {
  if (els) return;
  const holder = document.createElement("div");
  holder.innerHTML = MARKUP.trim();
  document.body.appendChild(holder.firstElementChild);
  els = {
    overlay: $("ef-overlay"),
    form: $("ef-form"),
    title: $("ef-title"),
    subtitle: $("ef-subtitle"),
    date: $("ef-date"),
    weight: $("ef-weight"),
    weightUnit: $("ef-weight-unit"),
    kcal: $("ef-kcal"),
    warnings: $("ef-warnings"),
    error: $("ef-error"),
    save: $("ef-save"),
  };
  // Wire interactions once.
  $("ef-close").addEventListener("click", close);
  $("ef-cancel").addEventListener("click", close);
  els.date.addEventListener("change", (e) => {
    if (ctx.dateEditable) prefill(e.target.value);
  });
  els.overlay.addEventListener("click", (e) => {
    if (e.target === els.overlay) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.overlay.hidden) close();
  });
  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    save();
  });
}

/**
 * Open the entry modal.
 *
 * @param {object} opts
 * @param {string} opts.date        ISO date to open on
 * @param {string} [opts.units]     display unit ("kg" | "lb")
 * @param {boolean} [opts.dateEditable] true = new entry (date editable); false = edit (date fixed)
 * @param {() => (void|Promise<void>)} [opts.onSaved] called after a successful save so the caller re-fetches
 */
export async function openEntryForm({ date, units = "kg", dateEditable = true, onSaved = null }) {
  ensure();
  ctx.units = units;
  ctx.dateEditable = dateEditable;
  ctx.onSaved = onSaved;

  els.title.textContent = dateEditable ? "New Entry" : "Edit Entry";
  els.subtitle.textContent = dateEditable
    ? "Weight-only or calorie-only days are fine."
    : "Editing this day — the date is fixed (delete & re-add to change it).";
  els.error.hidden = true;
  els.warnings.hidden = true;
  els.save.disabled = false;
  els.save.textContent = "Save entry";
  els.weightUnit.textContent = unitLabel(units);
  els.date.value = date;
  els.date.readOnly = !dateEditable;
  els.date.classList.toggle("is-readonly", !dateEditable);
  els.weight.value = "";
  els.kcal.value = "";
  els.overlay.hidden = false;
  await prefill(date);
  els.weight.focus();
}
