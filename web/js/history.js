/**
 * History screen — a list of every logged entry (newest first) with edit/delete,
 * plus a weekly summary.
 *
 * Data sources are all EXISTING endpoints: `/entries` (raw weight + kcal per day),
 * `/estimate` (the smoothed `trend_kg` series), `/settings` (display units), and
 * `/tdee-history` (the rolling estimate as-of each day). No estimator math is
 * reimplemented here — the weekly TDEE is *selected* from `/tdee-history`, and the
 * simple aggregates (avg weight/intake, days logged, net trend change) are display
 * aggregation of data the page already holds.
 *
 * Derived-not-stored: every edit/delete calls `reload()`, which re-fetches all of
 * the above so the weekly TDEE and trend recompute. Other screens re-fetch on load,
 * so returning to the Dashboard/TDEE views reflects the change too.
 *
 * Week boundary: ISO weeks, Monday→Sunday.
 */

import { deleteEntry, getEstimate, getSettings, getTdeeHistory, listEntries } from "./api.js";
import { openEntryForm } from "./entry-form.js";
import { formatWeight, fromKg, unitLabel } from "./units.js";
import { aggregateWeeks } from "./weekly.js";

const $ = (id) => document.getElementById(id);

const state = { units: "kg", entries: [], trendByDate: {}, tdeeByDate: {} };

// ---------- helpers ---------- //

const fmtInt = (v) => Math.round(v).toLocaleString("en-US");

function fmtDay(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleString("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function fmtShort(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

// ---------- entries list ---------- //

function renderEntries() {
  const listEl = $("entries-list");
  const emptyEl = $("entries-empty");
  if (state.entries.length === 0) {
    listEl.innerHTML = "";
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;
  const u = state.units;
  const ul = unitLabel(u);

  // Newest first.
  const rows = [...state.entries].sort((a, b) => (a.date < b.date ? 1 : -1));
  listEl.innerHTML = rows
    .map((e) => {
      const w = e.weight_kg == null ? "—" : `${formatWeight(e.weight_kg, u)} ${ul}`;
      const k = e.kcal == null ? "—" : `${fmtInt(e.kcal)} kcal`;
      return `
      <div class="entry-row" data-row="${e.date}">
        <div class="entry-date">${fmtDay(e.date)}</div>
        <div class="entry-vals">
          <span class="entry-val">${w}</span>
          <span class="entry-dot">·</span>
          <span class="entry-val">${k}</span>
        </div>
        <div class="entry-actions">
          <button class="entry-btn" type="button" data-edit="${e.date}">Edit</button>
          <button class="entry-btn entry-btn-danger" type="button" data-del="${e.date}">Delete</button>
        </div>
        <div class="entry-confirm" hidden>
          <span class="entry-confirm-text">Delete this day permanently?</span>
          <button class="entry-btn entry-btn-danger" type="button" data-del-yes="${e.date}">Delete</button>
          <button class="entry-btn" type="button" data-del-no="${e.date}">Cancel</button>
        </div>
      </div>`;
    })
    .join("");
}

function rowEl(date) {
  return document.querySelector(`.entry-row[data-row="${date}"]`);
}

function showConfirm(date, show) {
  const row = rowEl(date);
  if (!row) return;
  row.querySelector(".entry-actions").hidden = show;
  row.querySelector(".entry-confirm").hidden = !show;
}

async function onDelete(date) {
  const row = rowEl(date);
  try {
    await deleteEntry(date);
    await reload();
  } catch (err) {
    // Keep the row; surface the failure at the page level.
    if (row) showConfirm(date, false);
    showError(`Couldn't delete ${date}: ${err.message}`);
  }
}

function wireEntriesList() {
  $("entries-list").addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.edit) {
      openEntryForm({ date: t.dataset.edit, units: state.units, dateEditable: false, onSaved: reload });
    } else if (t.dataset.del) {
      showConfirm(t.dataset.del, true);
    } else if (t.dataset.delNo) {
      showConfirm(t.dataset.delNo, false);
    } else if (t.dataset.delYes) {
      onDelete(t.dataset.delYes);
    }
  });
}

// ---------- weekly summary ---------- //

function renderWeekly() {
  const section = $("weekly-section");
  const listEl = $("weekly-list");
  if (state.entries.length === 0) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const u = state.units;
  const ul = unitLabel(u);

  // Pure bucketing + aggregation (weekly.js); this layer only formats for display.
  const weeks = aggregateWeeks(state.entries, state.trendByDate, state.tdeeByDate);
  listEl.innerHTML = weeks
    .map((wk) => {
      const range = `${fmtShort(wk.weekStart)} – ${fmtShort(wk.weekEnd)}`;
      const wDisp = (kg) => (kg == null ? "—" : `${formatWeight(kg, u)} ${ul}`);
      const changeDisp =
        wk.netChangeKg == null
          ? "—"
          : `${wk.netChangeKg > 0 ? "+" : wk.netChangeKg < 0 ? "−" : ""}${Math.abs(fromKg(wk.netChangeKg, u)).toFixed(1)} ${ul}`;

      return `
      <article class="card weekly-card">
        <header class="weekly-head">
          <span class="weekly-range">${range}</span>
          <span class="weekly-adherence">${wk.logged} / 7 days</span>
        </header>
        <div class="weekly-grid">
          <div class="weekly-stat"><span class="weekly-k">Avg weight</span><span class="weekly-v">${wDisp(wk.avgWeightKg)}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Avg trend</span><span class="weekly-v">${wDisp(wk.avgTrendKg)}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Trend change</span><span class="weekly-v">${changeDisp}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Avg intake</span><span class="weekly-v">${wk.avgIntake == null ? "—" : `${fmtInt(wk.avgIntake)} kcal`}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Est. TDEE</span><span class="weekly-v">${wk.weeklyTdee == null ? "—" : `${fmtInt(wk.weeklyTdee)} kcal`}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Est. deficit</span><span class="weekly-v">${wk.deficit == null ? "—" : `${wk.deficit >= 0 ? "" : "+"}${fmtInt(Math.abs(wk.deficit))} kcal`}</span></div>
        </div>
      </article>`;
    })
    .join("");
}

// ---------- load ---------- //

function showError(msg) {
  const el = $("page-error");
  el.textContent = msg;
  el.hidden = false;
}

async function reload() {
  const [entries, estimate, settings, history] = await Promise.all([
    listEntries(),
    getEstimate(),
    getSettings(),
    getTdeeHistory(),
  ]);
  state.entries = entries;
  state.units = settings.units === "lb" ? "lb" : "kg";
  state.trendByDate = {};
  for (const p of estimate.trend) state.trendByDate[p.date] = p.trend_kg;
  state.tdeeByDate = {};
  for (const p of history.points) state.tdeeByDate[p.date] = p;

  renderEntries();
  renderWeekly();
}

async function init() {
  wireEntriesList();
  try {
    await reload();
  } catch (err) {
    showError(`Couldn't load history: ${err.message}`);
  }
}

init();
