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

function mean(nums) {
  const vals = nums.filter((n) => n != null && !Number.isNaN(n));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

/** Monday (UTC) of the ISO week containing `iso`, as YYYY-MM-DD. */
function weekStart(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const dow = (d.getUTCDay() + 6) % 7; // 0 = Monday … 6 = Sunday
  d.setUTCDate(d.getUTCDate() - dow);
  return d.toISOString().slice(0, 10);
}

function addDaysIso(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
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

  // Group entries by ISO-week Monday.
  const byWeek = new Map();
  for (const e of state.entries) {
    const wk = weekStart(e.date);
    if (!byWeek.has(wk)) byWeek.set(wk, []);
    byWeek.get(wk).push(e);
  }

  const weeks = [...byWeek.keys()].sort((a, b) => (a < b ? 1 : -1)); // newest first
  listEl.innerHTML = weeks
    .map((wk) => {
      const days = byWeek.get(wk);
      const sunday = addDaysIso(wk, 6);
      const range = `${fmtShort(wk)} – ${fmtShort(sunday)}`;
      const logged = days.length;

      const avgW = mean(days.map((d) => d.weight_kg));
      const avgK = mean(days.map((d) => d.kcal));

      // Trend values for this week's days (from /estimate), for the average and the
      // net change (first→last available trend within the week).
      const trends = days
        .map((d) => ({ date: d.date, t: state.trendByDate[d.date] }))
        .filter((x) => x.t != null)
        .sort((a, b) => (a.date < b.date ? -1 : 1));
      const avgT = mean(trends.map((x) => x.t));
      const netChange = trends.length >= 2 ? trends[trends.length - 1].t - trends[0].t : null;

      // Weekly TDEE: the most recent rolling estimate within the week that the
      // estimator actually produced. "—" when it declined all week (early/pre-window).
      let weeklyTdee = null;
      for (const d of [...days].sort((a, b) => (a.date < b.date ? -1 : 1))) {
        const h = state.tdeeByDate[d.date];
        if (h != null && h.tdee != null) weeklyTdee = h.tdee;
      }
      const deficit = weeklyTdee != null && avgK != null ? weeklyTdee - avgK : null;

      const wDisp = (kg) => (kg == null ? "—" : `${formatWeight(kg, u)} ${ul}`);
      const changeDisp =
        netChange == null
          ? "—"
          : `${netChange > 0 ? "+" : netChange < 0 ? "−" : ""}${Math.abs(fromKg(netChange, u)).toFixed(1)} ${ul}`;

      return `
      <article class="card weekly-card">
        <header class="weekly-head">
          <span class="weekly-range">${range}</span>
          <span class="weekly-adherence">${logged} / 7 days</span>
        </header>
        <div class="weekly-grid">
          <div class="weekly-stat"><span class="weekly-k">Avg weight</span><span class="weekly-v">${wDisp(avgW)}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Avg trend</span><span class="weekly-v">${wDisp(avgT)}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Trend change</span><span class="weekly-v">${changeDisp}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Avg intake</span><span class="weekly-v">${avgK == null ? "—" : `${fmtInt(avgK)} kcal`}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Est. TDEE</span><span class="weekly-v">${weeklyTdee == null ? "—" : `${fmtInt(weeklyTdee)} kcal`}</span></div>
          <div class="weekly-stat"><span class="weekly-k">Est. deficit</span><span class="weekly-v">${deficit == null ? "—" : `${deficit >= 0 ? "" : "+"}${fmtInt(Math.abs(deficit))} kcal`}</span></div>
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
