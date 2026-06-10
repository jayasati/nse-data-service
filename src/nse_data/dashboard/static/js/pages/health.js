// Health dashboard page controller.
//
// One /api/health poll every 15s drives a grouped, filterable, sortable table.
// All view state (status filter, search, sort, collapsed groups, expanded
// drill-downs) lives here so a refresh re-renders without losing the user's place.
import "../components/llm_badge.js";
import { $, ago } from "../core/util.js";
import { initThemeToggle } from "../core/theme.js";

const COLORS = { ok: "--ok", stale: "--stale", down: "--down", no_table: "--no_table",
                 empty: "--empty", disabled: "--disabled", unknown: "--unknown" };
const LABEL = { ok: "OK", stale: "STALE", down: "DOWN", no_table: "NO TABLE",
                empty: "EMPTY", disabled: "DISABLED", unknown: "UNKNOWN" };
// Worst-first; also the group render order.
const ORDER = ["down", "no_table", "stale", "empty", "unknown", "ok", "disabled"];
// Cadence tokens -> seconds, for sorting the Cadence column sensibly.
const CADENCE_SECS = { "15s": 15, "30s": 30, "1m": 60, "3m": 180, "5m": 300,
                       "10m": 600, "30m": 1800, "1h": 3600, daily: 86400, weekly: 604800 };

const color = st => getComputedStyle(document.documentElement)
  .getPropertyValue(COLORS[st] || "--unknown").trim();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

// ---- view state ----
let data = null;                       // last /api/health report
const filter = new Set();              // active status filter (empty = all)
let query = "";                        // search text (lowercased)
let sort = { key: null, dir: 1 };      // null => server order (worst-first, name)
const collapsed = new Set();           // collapsed status groups
const expanded = new Set();            // expanded collector rows (by name)
const previews = new Map();            // table -> { at, html } drill-down cache

// ---- data fetch ----
async function refresh() {
  let r;
  try { r = await (await fetch("/api/health", { cache: "no-store" })).json(); }
  catch (e) { $("meta").textContent = "fetch failed: " + e; return; }
  data = r;
  const gen = new Date(r.generated_at);
  $("meta").textContent =
    `${r.total} collectors · freshest feed ${ago(r.freshest_age_seconds)} ago · generated ${gen.toLocaleTimeString()}`;
  render();
}

// ---- predicates / ordering ----
function visible(c) {
  if (filter.size && !filter.has(c.status)) return false;
  if (query && !(`${c.name} ${c.table || ""}`.toLowerCase().includes(query))) return false;
  return true;
}

function sortKey(c) {
  switch (sort.key) {
    case "name": return c.name;
    case "cadence": return CADENCE_SECS[c.cadence] ?? Number.MAX_SAFE_INTEGER;
    case "rows": return c.rows ?? -1;
    case "age": return c.data_age_seconds ?? -1;
    case "lag": return c.lag_seconds ?? -1;
    default: return 0;
  }
}
function ordered(list) {
  if (!sort.key) return list;                       // keep server worst-first
  return [...list].sort((a, b) => {
    const ka = sortKey(a), kb = sortKey(b);
    if (ka < kb) return -sort.dir;
    if (ka > kb) return sort.dir;
    return a.name.localeCompare(b.name);
  });
}

// ---- rendering ----
function freshnessBar(c) {
  if (c.lag_seconds == null) return '<span class="tbl">—</span>';
  const tol = c.tolerance_seconds || 0;
  const ratio = tol ? clamp(c.lag_seconds / (4 * tol), 0, 1) : 0;
  const w = Math.max(ratio * 100, 3);
  const lag = c.lag_seconds > 0 ? ago(c.lag_seconds) : "fresh";
  return `<span class="lag" title="lag ${lag} of ${ago(tol)} tolerance">` +
         `<span class="lagbar"><i style="width:${w.toFixed(0)}%;background:${color(c.status)}"></i></span>` +
         `<span class="lagtxt">${lag}</span></span>`;
}

function rowHtml(c) {
  const expandable = !!c.table;
  const tcell = c.table ? `<span class="tbl">${esc(c.table)}</span>` : '<span class="tbl">—</span>';
  const caret = expandable ? `<span class="caret">${expanded.has(c.name) ? "▾" : "▸"}</span>` : '<span class="caret-sp"></span>';
  let html =
    `<tr class="row${expandable ? " clickable" : ""}" data-name="${esc(c.name)}" data-table="${esc(c.table || "")}">` +
      `<td class="name">${caret}<span class="dot" style="background:${color(c.status)}"></span>${esc(c.name)}</td>` +
      `<td>${tcell}</td>` +
      `<td class="tbl">${esc(c.cadence || "—")}</td>` +
      `<td class="num">${c.rows != null ? c.rows.toLocaleString() : "—"}</td>` +
      `<td>${ago(c.data_age_seconds)}${c.data_age_seconds != null ? " ago" : ""}</td>` +
      `<td>${freshnessBar(c)}</td>` +
    `</tr>`;
  if (expandable && expanded.has(c.name)) {
    html += `<tr class="detail" data-for="${esc(c.name)}"><td colspan="6">` +
            `<div class="preview" id="pv-${esc(c.name)}">loading…</div></td></tr>`;
  }
  return html;
}

function groupHeaderHtml(st, n) {
  const isC = collapsed.has(st);
  return `<tr class="group" data-status="${st}">` +
    `<td colspan="6"><span class="gcaret">${isC ? "▸" : "▾"}</span>` +
    `<span class="dot" style="background:${color(st)}"></span>` +
    `<span class="glabel" style="color:${color(st)}">${LABEL[st]}</span>` +
    `<span class="gcount">${n}</span></td></tr>`;
}

function renderSummary() {
  const sum = $("summary"); sum.innerHTML = "";
  for (const st of ORDER) {
    const n = data.summary[st]; if (!n) continue;
    const p = document.createElement("button");
    p.className = "pill" + (filter.has(st) ? " active" : "");
    p.dataset.status = st;
    p.innerHTML = `<span class="dot" style="background:${color(st)}"></span>${LABEL[st]} <b>${n}</b>`;
    sum.appendChild(p);
  }
}

function render() {
  if (!data) return;
  renderSummary();
  // sort indicators on headers
  document.querySelectorAll("th.sortable").forEach(th => {
    th.classList.toggle("asc", sort.key === th.dataset.sort && sort.dir === 1);
    th.classList.toggle("desc", sort.key === th.dataset.sort && sort.dir === -1);
  });

  const byStatus = {};
  for (const c of data.collectors) if (visible(c)) (byStatus[c.status] ||= []).push(c);

  const html = [];
  let shown = 0;
  for (const st of ORDER) {
    const list = byStatus[st]; if (!list || !list.length) continue;
    shown += list.length;
    html.push(groupHeaderHtml(st, list.length));
    if (!collapsed.has(st)) for (const c of ordered(list)) html.push(rowHtml(c));
  }
  const tb = $("rows");
  tb.innerHTML = shown
    ? html.join("")
    : `<tr><td colspan="6" class="none">no collectors match this filter</td></tr>`;

  // fill any expanded drill-downs (cached)
  for (const c of data.collectors)
    if (expanded.has(c.name) && c.table && visible(c) && !collapsed.has(c.status))
      fillPreview(c.name, c.table);
}

// ---- drill-down preview ----
async function fillPreview(name, table) {
  const el = document.getElementById("pv-" + name);
  if (!el) return;
  const cached = previews.get(table);
  if (cached && Date.now() - cached.at < 30000) { el.innerHTML = cached.html; return; }
  try {
    const d = await (await fetch(`/api/table/${table}?limit=25`, { cache: "no-store" })).json();
    const html = previewHtml(d, table);
    previews.set(table, { at: Date.now(), html });
    el.innerHTML = html;
  } catch (e) { el.textContent = "preview failed: " + e; }
}

function previewHtml(d, table) {
  if (!d.rows || !d.rows.length) return '<span class="tbl">table is empty</span>';
  const cols = Object.keys(d.rows[0]);                       // full row — every column
  const isNum = v => v !== null && v !== "" && typeof v !== "boolean" && !isNaN(v);
  const head = cols.map(c => `<th>${esc(c)}</th>`).join("");
  const body = d.rows.map(r =>
    "<tr>" + cols.map(c =>
      `<td class="${isNum(r[c]) ? "n" : ""}">${esc(r[c])}</td>`).join("") + "</tr>").join("");
  return `<div class="pv-head">latest ${d.rows.length} rows · ${cols.length} columns · ordered by ` +
         `<span class="tbl">${esc(d.order_by)} ↓</span> · ` +
         `<a class="tbl" href="/api/table/${table}" target="_blank">open JSON ↗</a></div>` +
         `<div class="pv-scroll"><table class="pv"><thead><tr>${head}</tr></thead>` +
         `<tbody>${body}</tbody></table></div>`;
}

// ---- events (delegated) ----
$("summary").addEventListener("click", e => {
  const pill = e.target.closest(".pill"); if (!pill) return;
  const st = pill.dataset.status;
  filter.has(st) ? filter.delete(st) : filter.add(st);
  render();
});

$("search").addEventListener("input", e => { query = e.target.value.trim().toLowerCase(); render(); });

document.querySelector("thead").addEventListener("click", e => {
  const th = e.target.closest("th.sortable"); if (!th) return;
  const key = th.dataset.sort;
  if (sort.key === key) sort.dir = -sort.dir;
  else sort = { key, dir: key === "name" ? 1 : -1 };   // numeric cols default high-first
  render();
});

$("rows").addEventListener("click", e => {
  const grp = e.target.closest("tr.group");
  if (grp) { const st = grp.dataset.status; collapsed.has(st) ? collapsed.delete(st) : collapsed.add(st); render(); return; }
  const row = e.target.closest("tr.row.clickable");
  if (row) { const n = row.dataset.name; expanded.has(n) ? expanded.delete(n) : expanded.add(n); render(); }
});

initThemeToggle("themeBtn", render);  // re-color on toggle (inline colours)
refresh();
setInterval(refresh, 15000);
