// Test-gate ladder page controller.
//
// Polls /api/gates every 30s and renders the G0–G12 acceptance ladder from
// plans/GRAND_TESTING_STRATEGY.md. Auto gates re-check live against the DB;
// manual gates show their recorded verdict. Summary pills filter by status.
import { $ } from "../core/util.js";
import { initThemeToggle } from "../core/theme.js";

// gate status -> theme color var (reuse health palette) + label
const COLORS = { pass: "--ok", fail: "--down", warn: "--stale", untested: "--unknown" };
const LABEL = { pass: "PASS", fail: "FAIL", warn: "WARN", untested: "UNTESTED" };
const ORDER = ["fail", "warn", "untested", "pass"];   // worst-first for the summary

const color = st => getComputedStyle(document.documentElement)
  .getPropertyValue(COLORS[st] || "--unknown").trim();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let data = null;
const filter = new Set();              // active status filter (empty = all)

async function refresh() {
  let r;
  try { r = await (await fetch("/api/gates", { cache: "no-store" })).json(); }
  catch (e) { $("meta").textContent = "fetch failed: " + e; return; }
  data = r;
  const s = r.summary;
  const blocked = r.gates.find(g => g.status === "fail" || g.status === "untested");
  $("meta").textContent =
    `${s.passed}/${s.total} gates green` +
    (blocked ? ` · next blocker: ${blocked.gate_id} ${blocked.name}` : " · ladder clear");
  render();
}

function fmtChecked(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? esc(iso) : d.toLocaleString();
}

function rowHtml(g) {
  const c = color(g.status);
  const profit = g.gate_id === "G12" ? " profit" : "";
  return `<tr class="gaterow${profit}">` +
    `<td class="gnum">${esc(g.gate_id)}</td>` +
    `<td class="name"><span class="dot" style="background:${c}"></span>${esc(g.name)}</td>` +
    `<td class="gmode">${esc(g.mode)}</td>` +
    `<td class="gstatus" style="color:${c}">${LABEL[g.status] || esc(g.status)}</td>` +
    `<td class="gdetail">${esc(g.detail)}</td>` +
    `<td class="gchecked">${fmtChecked(g.checked_at)}</td>` +
  `</tr>`;
}

function renderSummary() {
  const counts = {};
  for (const g of data.gates) counts[g.status] = (counts[g.status] || 0) + 1;
  const sum = $("summary"); sum.innerHTML = "";
  for (const st of ORDER) {
    const n = counts[st]; if (!n) continue;
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
  const rows = data.gates.filter(g => !filter.size || filter.has(g.status));
  $("rows").innerHTML = rows.length
    ? rows.map(rowHtml).join("")
    : `<tr><td colspan="6" class="none">no gates match this filter</td></tr>`;
}

$("summary").addEventListener("click", e => {
  const pill = e.target.closest(".pill"); if (!pill) return;
  const st = pill.dataset.status;
  filter.has(st) ? filter.delete(st) : filter.add(st);
  render();
});

initThemeToggle("themeBtn", render);   // re-color on toggle (inline colours)
refresh();
setInterval(refresh, 30000);
