// LLM spend page controller — one /api/llm/spend poll a minute drives the
// cards, the 30-day bar strip, and the daily/monthly tables.
import { $ } from "../core/util.js";
import { initThemeToggle } from "../core/theme.js";

const usd = v => "$" + (+v).toLocaleString("en-US",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function daysInMonthSoFar(month, todayIso) {
  // for the current month, average over elapsed days; past months over their length.
  if (todayIso.startsWith(month)) return +todayIso.slice(8, 10);
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m, 0).getDate();
}

function render(r) {
  const cap = r.cap_usd, today = r.today;
  $("meta").textContent = `daily cap ${usd(cap)} · log: data/llm_spend.json`;
  $("today").textContent = usd(today.spend_usd);
  const pct = cap ? Math.min(100, today.spend_usd / cap * 100) : 0;
  const fill = $("capFill");
  fill.style.width = pct.toFixed(1) + "%";
  fill.className = "fill" + (pct >= 90 ? " hot" : pct >= 60 ? " warn" : "");
  $("capNote").textContent =
    `${pct.toFixed(0)}% of cap · ${usd(today.remaining_usd)} left today`;

  const thisMonth = r.monthly.find(m => today.date.startsWith(m.month));
  $("month").textContent = usd(thisMonth ? thisMonth.usd : 0);
  $("monthNote").textContent = thisMonth
    ? `avg ${usd(thisMonth.usd / daysInMonthSoFar(thisMonth.month, today.date))}/day`
    : "no calls yet this month";
  $("total").textContent = usd(r.total_usd);
  $("totalNote").textContent = `${r.daily.length} day(s) on record`;

  // 30-day strip, oldest → newest.
  const days = [...r.daily.slice(0, 30)].reverse();
  const max = Math.max(cap * 0.25, ...days.map(d => d.usd));
  $("bars").innerHTML = days.map(d => {
    const h = Math.max(2, d.usd / max * 100);
    return `<div class="bar" style="height:${h.toFixed(1)}%">` +
           `<span class="tip">${esc(d.date)} · ${usd(d.usd)}</span></div>`;
  }).join("") || `<span style="color:var(--dim);font-size:12px">no spend recorded yet</span>`;

  $("dailyRows").innerHTML = r.daily.map(d =>
    `<tr><td>${esc(d.date)}</td><td class="num">${usd(d.usd)}</td>` +
    `<td class="num">${cap ? (d.usd / cap * 100).toFixed(0) + "%" : "—"}</td></tr>`
  ).join("") || `<tr><td colspan="3" style="color:var(--dim)">no spend recorded yet</td></tr>`;

  $("monthlyRows").innerHTML = r.monthly.map(m =>
    `<tr><td>${esc(m.month)}</td><td class="num">${usd(m.usd)}</td>` +
    `<td class="num">${usd(m.usd / daysInMonthSoFar(m.month, today.date))}</td></tr>`
  ).join("") || `<tr><td colspan="3" style="color:var(--dim)">no spend recorded yet</td></tr>`;
}

async function refresh() {
  try {
    render(await (await fetch("/api/llm/spend", { cache: "no-store" })).json());
  } catch (e) {
    $("meta").textContent = "fetch failed: " + e;
  }
}

initThemeToggle();
refresh();
setInterval(refresh, 60_000);
