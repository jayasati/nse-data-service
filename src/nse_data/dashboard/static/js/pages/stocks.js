// Per-stock page controller. Owns app state (symbol/timeframe/mode/overlays)
// and wires the DOM to ChartController, SearchBox and the API.
import { $, fmt, fmt2 } from "../core/util.js";
import { initThemeToggle } from "../core/theme.js";
import { Api } from "../core/api.js";
import { ChartController } from "../components/chart.js";
import { SearchBox } from "../components/search.js";

const chart = new ChartController("chart", "panes", "tip");

let current = null, tf = "1D", mode = "area", lastBars = [];
const overlays = { vwap: false, ema20: false, ema50: false, ema200: false, bb: false };
let wl = new Set(JSON.parse(localStorage.getItem("nse_watchlist") || "[]"));

const MODES = ["area", "candle", "ha"];
const MODE_LABEL = { area: "📈 Line", candle: "📊 Candle", ha: "📊 Heikin-Ashi" };
const OV_COLOR = { vwap: "#7c6cdb", ema20: "#f59e0b", ema50: "#3b82f6", ema200: "#ec4899", bb: "#94a3b8" };
const STUDY_LABEL = { rsi: "RSI 14", macd: "MACD", adx: "ADX", chop: "CHOP" };

// timeframe -> {interval, days} fetch plan.
function plan() {
  if (tf === "1D") return mode === "area" ? { interval: "1m", days: 4 } : { interval: "5m", days: 4 };
  const d = { "1W": 7, "1M": 23, "3M": 66, "6M": 132, "1Y": 260, "3Y": 1100, "5Y": 1900, "All": 6000 }[tf] || 260;
  return { interval: "1d", days: d };
}

async function loadChart() {
  if (!current) return;
  const pl = plan();
  const r = await Api.history(current, pl.interval, pl.days);
  const pts = r.points || [];
  lastBars = pts.map(p => r.type === "line"
    ? { time: p.time, open: p.value, high: p.value, low: p.value, close: p.value, volume: p.volume }
    : { time: p.time, open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume });
  if (!lastBars.length) {
    $("syminfo").textContent = `${tf} · no data (intraday needs a live session)`;
    chart.render([], mode); return;
  }
  const info = chart.render(lastBars, mode);
  chart.drawIndicators(lastBars, tf, overlays);
  $("syminfo").innerHTML = `${info.count} bars · <span class="${info.up ? "up" : "down"}">${info.chg >= 0 ? "+" : ""}${info.chg.toFixed(1)}%</span> over ${tf}`;
}

async function loadMeta() {
  if (!current) return;
  let m; try { m = await Api.meta(current); } catch (e) { return; }
  $("sym").textContent = m.symbol; $("exch").textContent = m.exchange || "NSE";
  $("name").textContent = m.name || ""; $("ltp").textContent = fmt2(m.ltp);
  $("meta").innerHTML = m.is_live
    ? `<span style="color:var(--up)">● LIVE</span> ${new Date().toLocaleTimeString()}` : "EOD data";
  const cp = $("chgpill"), up = (m.change || 0) >= 0;
  cp.className = "chgpill " + (up ? "up" : "down");
  cp.textContent = `${up ? "▲" : "▼"} ${fmt2(Math.abs(m.change))} (${up ? "+" : ""}${m.change_pct == null ? "—" : m.change_pct}%)`;
  $("phprice").classList.toggle("live", !!m.is_live);
  $("livedot").style.display = m.is_live ? "inline-block" : "none";
  $("ohlc").innerHTML = [["O", m.open], ["H", m.high], ["L", m.low], ["Prev", m.prev_close], ["Vol", m.volume]]
    .map(([k, v]) => `<span><span class="k">${k}</span><b>${k === "Vol" ? fmt(v) : fmt2(v)}</b></span>`).join("");
  const pos = (m.week52_low != null && m.week52_high != null && m.week52_high > m.week52_low)
    ? Math.max(0, Math.min(100, (m.ltp - m.week52_low) / (m.week52_high - m.week52_low) * 100)) : null;
  const cells = [
    ["Market Cap", m.market_cap_cr != null ? "₹" + fmt(m.market_cap_cr) + " Cr" : "—"],
    ["P/E", m.pe != null ? fmt2(m.pe) : "—"],
    ["P/B", m.pb != null ? fmt2(m.pb) : "—"],
    ["52W High", fmt2(m.week52_high)],
    ["52W Low", fmt2(m.week52_low)],
    ["Div Yield", m.div_yield != null ? fmt2(m.div_yield) + "%" : "—"],
    ["Sector", m.sector || "—"],
    ["Delivery %", m.delivery_pct != null ? fmt2(m.delivery_pct) + "%" : "—"],
    ["ROE", m.roe != null ? fmt2(m.roe) + "%" : "—"],
  ];
  $("fundaGrid").innerHTML = cells.map(([k, v]) => {
    const bar = (k === "52W High" && pos != null) ? `<div class="bar52"><i style="left:${pos}%"></i></div>` : "";
    return `<div class="cell"><div class="k">${k}</div><div class="v">${v}</div>${bar}</div>`;
  }).join("");
  updateStar();
}

function select(sym) { current = sym; $("empty").style.display = "none"; loadChart(); loadMeta(); updateStar(); }

// ---- watchlist (per-stock star) ----
const saveWl = () => localStorage.setItem("nse_watchlist", JSON.stringify([...wl]));
function toggleWl(sym) { if (wl.has(sym)) wl.delete(sym); else wl.add(sym); saveWl(); updateStar(); }
function updateStar() { const b = $("starBtn"), on = current && wl.has(current); b.textContent = on ? "★" : "☆"; b.classList.toggle("on", !!on); }
$("starBtn").onclick = () => { if (current) toggleWl(current); };

// ---- chart mode ----
function setMode(m) {
  mode = m;
  document.querySelectorAll("#modes button").forEach(b => b.classList.toggle("on", b.dataset.m === m));
  $("candleBtn").innerHTML = MODE_LABEL[m];
  if (current) loadChart();
}
$("modes").onclick = e => { if (e.target.tagName === "BUTTON") setMode(e.target.dataset.m); };
$("candleBtn").onclick = () => setMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length]);

// ---- timeframe & terminal ----
$("tfs").onclick = e => {
  if (e.target.tagName !== "BUTTON") return;
  tf = e.target.dataset.t;
  document.querySelectorAll("#tfs button").forEach(b => b.classList.toggle("on", b === e.target));
  $("tftag").textContent = tf;
  if (current) loadChart();
};
$("termBtn").onclick = () => {
  const on = document.body.classList.toggle("terminal");
  $("termBtn").classList.toggle("on", on);
  requestAnimationFrame(() => chart.fit());
};

// ---- overlays & study panes ----
$("overlays").onclick = e => {
  if (e.target.tagName !== "BUTTON") return;
  const o = e.target.dataset.o; overlays[o] = !overlays[o];
  e.target.classList.toggle("on", overlays[o]);
  e.target.style.background = overlays[o] ? OV_COLOR[o] : "";
  e.target.style.borderColor = overlays[o] ? OV_COLOR[o] : "";
  if (current) chart.drawIndicators(lastBars, tf, overlays);
};
$("studies").onclick = e => {
  if (e.target.tagName !== "BUTTON") return;
  const s = e.target.dataset.s, on = !e.target.classList.contains("on");
  e.target.classList.toggle("on", on);
  e.target.style.background = on ? "#7c6cdb" : "";
  e.target.style.borderColor = on ? "#7c6cdb" : "";
  if (on) chart.addStudy(s, STUDY_LABEL[s]); else chart.removeStudy(s);
  chart.fit();
  if (current) chart.drawIndicators(lastBars, tf, overlays);
};
$("fundaHead").onclick = () => $("funda").classList.toggle("collapsed");

// ---- compose ----
new SearchBox({ inputId: "search", dropdownId: "dropdown", onSelect: select, inWatchlist: s => wl.has(s) });
initThemeToggle("themeBtn", () => chart.themeAll());
chart.themeAll();  // sync chart to the (possibly persisted) theme on load

// Open with the most-traded stock so the page isn't empty.
(async () => { try { const r = await Api.search(""); if (r.results && r.results.length) select(r.results[0].symbol); } catch (e) {} })();
// Live refresh of the open stock (price + chart) while the feed updates.
setInterval(() => { if (current) { loadMeta(); loadChart(); } }, 60000);
