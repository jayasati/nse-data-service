// Per-stock page controller. Owns app state (symbol/timeframe/mode/overlays)
// and wires the DOM to ChartController, SearchBox and the API.
import "../components/llm_badge.js";
import { $, fmt, fmt2 } from "../core/util.js";
import { initThemeToggle } from "../core/theme.js";
import { Api } from "../core/api.js";
import { ChartController } from "../components/chart.js";
import { SearchBox } from "../components/search.js";
import { initCockpit, setCockpitSymbol } from "./stock_tabs.js";

const chart = new ChartController("chart", "panes", "tip");

let current = null, tf = "1D", mode = "area", lastBars = [];
// As-of date (YYYY-MM-DD, IST) for point-in-time verification. null = live.
// When set, the chart + server-indicator overlays are anchored to that date.
let asof = null;
// Intraday bar size for the 1D timeframe: "5m" (default, indicators comparable
// with Groww/TV) or "1m" (a new candle every minute). Persisted across visits.
let bar = localStorage.getItem("nse_intraday_bar") === "1m" ? "1m" : "5m";
// There are intentionally NO client-computed overlays (VWAP/EMA/Bollinger
// used to be recomputed in the browser from whatever bars the chart loaded —
// 1m vs 5m basis ≠ same value — and disagreed with the server chips). The
// indicator_* tables (srv chips) are the single source of truth — the same
// rows the prediction bot reads.
let wl = new Set(JSON.parse(localStorage.getItem("nse_watchlist") || "[]"));

// Server-side indicators state. `data` is the last /indicators payload for
// the current symbol; `enabled` is the set of "indicator.column" keys the
// user has toggled on. Persisted across symbol switches so the user doesn't
// lose their preferred overlays.
const srv = { data: null, enabled: new Set(JSON.parse(localStorage.getItem("nse_srv_overlays") || "[]")) };
const saveSrv = () => localStorage.setItem("nse_srv_overlays", JSON.stringify([...srv.enabled]));

const MODES = ["area", "candle", "ha"];
const MODE_LABEL = { area: "📈 Line", candle: "📊 Candle", ha: "📊 Heikin-Ashi" };
// There are intentionally NO client-computed studies (RSI/MACD/ADX/CHOP used
// to be recomputed in the browser from the loaded bars — 14 one-min bars = a
// 14-minute RSI — and disagreed with the server values). The indicator_*
// tables (srv chips, intraday + EOD) are the single source of truth the bot
// reads.

// timeframe -> {interval, days} fetch plan. `days` counts TRADING days:
// the server returns the newest N intraday sessions / N EOD rows, so
// weekends and holidays never shrink the window.
function plan() {
  // 1D uses the `bar` toggle (5m default, or 1m). 5-min keeps RSI/MACD/ADX
  // comparable with Groww/TV (RSI 14 = a 70-min window vs only 14 min on
  // 1-min bars), so it's the default; 1m is opt-in for a candle every minute.
  // The render mode (line/candle/ha) does NOT change the bar size — that kept
  // the indicator basis stable when only the chart style changed.
  // 1D = the live session while the market is open, else the last trading day.
  // Bar size scales with the window: 1W = 15-min bars, 1M = 30-min bars,
  // 3M+ = daily bars.
  if (tf === "1D") return { interval: bar, days: 1 };
  if (tf === "1W") return { interval: "15m", days: 7 };
  if (tf === "1M") return { interval: "30m", days: 22 };
  const d = { "3M": 64, "6M": 126, "1Y": 250, "3Y": 750, "5Y": 1250, "All": 6000 }[tf] || 250;
  return { interval: "1d", days: d };
}

async function loadChart() {
  if (!current) return;
  const pl = plan();
  const r = await Api.history(current, pl.interval, pl.days, asof);
  const pts = r.points || [];
  lastBars = pts.map(p => r.type === "line"
    ? { time: p.time, open: p.value, high: p.value, low: p.value, close: p.value, volume: p.volume }
    : { time: p.time, open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume });
  if (!lastBars.length) {
    $("syminfo").textContent = `${tf} · no data (intraday needs a live session)`;
    chart.render([], mode); return;
  }
  const info = chart.render(lastBars, mode);
  drawSrvOverlays();
  const asofTag = asof ? ` · as of ${asof}` : "";
  $("syminfo").innerHTML = `${info.count} bars · <span class="${info.up ? "up" : "down"}">${info.chg >= 0 ? "+" : ""}${info.chg.toFixed(1)}%</span> over ${tf}${asofTag}`;
}

// ---- server-computed indicator overlays ----
// The chart's time axis matches one of two indicator cadences:
//   • 1D/1W/1M (intraday bars) → 5-min indicator rows (epoch ts + IST_OFFSET);
//     drawSrvOverlays clips them to the chart's bar times, and 15m/30m bar
//     starts are 5-min aligned, so the surviving points sit on the bars.
//   • 3M+ (daily bars)         → EOD rows (date strings)
const tfCadence = () => (["1D", "1W", "1M"].includes(tf) ? "intraday" : "eod");

async function loadSrvIndicators() {
  // Drop the previous cadence's overlays/sub-panes before reloading. A 1D→3M
  // switch changes the indicator set (intraday `rsi_5m` → daily `rsi`), and a
  // pane the new payload never mentions would otherwise never be hidden by
  // drawSrvOverlays — it lingers in the time-sync group carrying its old
  // intraday epoch range and drags the daily price chart's visible window down
  // to that 2-day slice (the "3M shows only today" bug). Cleared synchronously
  // here (before any await) so it's gone before loadChart renders the bars.
  chart.clearServerSeries();
  srv.data = null;
  if (!current) { renderSrvButtons(); return; }
  // Request enough rows to fill the chart with a little headroom on the left.
  // For intraday: one session is ≤75 5-min rows, so 75/session + headroom.
  // For daily: ask for the visible timeframe's days +5.
  const pl = plan();
  const cadence = tfCadence();
  const limit = cadence === "intraday" ? pl.days * 75 + 25 : pl.days + 5;
  try { srv.data = await Api.indicators(current, limit, cadence, asof); }
  catch (e) { srv.data = null; }
  renderSrvButtons();
  drawSrvOverlays();
}

// Build one toggle button per (indicator, column) for overlays (SMA), or per
// indicator family for oscillators (RSI, MACD — all columns share one pane).
// Indicator order follows the registry; new backend indicators show up here
// automatically.
//
// On intraday timeframes (1D) the chart axis is epoch seconds while server
// indicators carry date strings — they can't align, so we show a one-liner
// hint instead of buttons that would silently do nothing when clicked.
function renderSrvButtons() {
  const box = $("srv-indicators"); box.innerHTML = "";
  if (!srv.data || !srv.data.indicators) return;
  // The endpoint already filtered to the right cadence — no client-side check
  // needed. If the backend has no indicators of this cadence yet (e.g. session
  // indicators not built), the buttons row will just be empty.
  let idx = 0;
  for (const [name, block] of Object.entries(srv.data.indicators)) {
    if (block.pane === "oscillator") {
      // One button per indicator family. Toggles the entire sub-pane on/off.
      const key = `${name}.*`;
      const color = chart.srvColor(idx++);
      const on = srv.enabled.has(key);
      const btn = document.createElement("button");
      btn.textContent = name.toUpperCase();
      btn.title = `${name} (server, oscillator pane)`;
      btn.classList.toggle("on", on);
      if (on) { btn.style.background = color; btn.style.borderColor = color; }
      btn.onclick = () => toggleSrv(key, color, btn);
      box.appendChild(btn);
    } else {
      // Overlay: one button per price-level column (SMA20 / SMA50 / …).
      // `column_panes` reroutes mixed-scale columns: "hidden" = API-only
      // (state flags the bot reads directly), any other value = a named
      // sub-pane group rendered like an oscillator (ADX + DI± together).
      // _dir columns never get a button — they color their parent line.
      const colPane = block.column_panes || {};
      for (const col of block.columns) {
        if (col.endsWith("_dir") || colPane[col]) continue;
        const key = `${name}.${col}`;
        const color = chart.srvColor(idx++);
        const on = srv.enabled.has(key);
        const btn = document.createElement("button");
        btn.textContent = col.toUpperCase();
        btn.title = `${name} → ${col} (server)`;
        btn.classList.toggle("on", on);
        if (on) { btn.style.background = color; btn.style.borderColor = color; }
        btn.onclick = () => toggleSrv(key, color, btn);
        box.appendChild(btn);
      }
      for (const [group, cols] of Object.entries(paneGroups(block))) {
        const key = `${name}.${group}.*`;
        const color = chart.srvColor(idx++);
        const on = srv.enabled.has(key);
        const btn = document.createElement("button");
        btn.textContent = group.toUpperCase();
        btn.title = `${name} → ${cols.join(", ")} (server, sub-pane)`;
        btn.classList.toggle("on", on);
        if (on) { btn.style.background = color; btn.style.borderColor = color; }
        btn.onclick = () => toggleSrv(key, color, btn);
        box.appendChild(btn);
      }
    }
  }
}

// Sub-pane groups of an overlay block: {group: [cols...]} from column_panes,
// "hidden" excluded. Insertion order follows block.columns, so the color
// cursor (idx) advances identically in renderSrvButtons and drawSrvOverlays.
function paneGroups(block) {
  const groups = {};
  const colPane = block.column_panes || {};
  for (const col of block.columns) {
    const g = colPane[col];
    if (!g || g === "hidden") continue;
    (groups[g] = groups[g] || []).push(col);
  }
  return groups;
}

function toggleSrv(key, color, btn) {
  if (srv.enabled.has(key)) { srv.enabled.delete(key); btn.classList.remove("on"); btn.style.background = ""; btn.style.borderColor = ""; }
  else { srv.enabled.add(key); btn.classList.add("on"); btn.style.background = color; btn.style.borderColor = color; }
  saveSrv(); drawSrvOverlays();
}

// Convert server payload → lightweight-charts series data, filter NaN/null,
// then push to the chart. The payload's `time_key` is `"date"` for EOD
// indicators (matches daily-bar chart time) or `"ts"` for intraday (already
// IST-offset by the service so it matches the chart's intraday epoch time).
function drawSrvOverlays() {
  if (!srv.data || !srv.data.indicators) return;
  // Clip indicator points to the chart's candle range — otherwise older
  // values stretch lightweight-charts' time axis past the first candle,
  // which reads as "broken" on the page.
  const candleTimes = new Set(lastBars.map(b => b.time));
  if (!candleTimes.size) { chart.clearServerSeries(); return; }
  let idx = 0;
  for (const [name, block] of Object.entries(srv.data.indicators)) {
    const tk = block.time_key || "date";
    if (block.pane === "oscillator") {
      const key = `${name}.*`;
      const color = chart.srvColor(idx++);
      if (!srv.enabled.has(key)) { chart.hideServerOscillator(name); continue; }
      const pointsByCol = {};
      for (const col of block.columns) {
        pointsByCol[col] = block.points
          .filter(p => p[col] != null && isFinite(p[col]) && candleTimes.has(p[tk]))
          .map(p => ({ time: p[tk], value: p[col] }));
      }
      chart.showServerOscillator(name, block.columns, pointsByCol, color);
    } else {
      const colPane = block.column_panes || {};
      for (const col of block.columns) {
        if (col.endsWith("_dir") || colPane[col]) continue;  // rerouted or state flag
        const key = `${name}.${col}`;
        const color = chart.srvColor(idx++);
        if (!srv.enabled.has(key)) { chart.hideServerSeries(key); continue; }
        // A sibling `<col>_dir` column (±1) colors the line per point — green
        // while long, red while short — matching how TV paints Supertrend.
        const dirCol = block.columns.includes(`${col}_dir`) ? `${col}_dir` : null;
        const pts = block.points
          .filter(p => p[col] != null && isFinite(p[col]) && candleTimes.has(p[tk]))
          .map(p => {
            const pt = { time: p[tk], value: p[col] };
            if (dirCol && p[dirCol] != null) pt.color = p[dirCol] > 0 ? "#00b386" : "#eb5b3c";
            return pt;
          });
        chart.showServerSeries(key, pts, color);
      }
      // Mixed-scale columns rerouted into named sub-pane groups (ADX + DI±,
      // OBV, ratios) — rendered exactly like a server oscillator family.
      for (const [group, cols] of Object.entries(paneGroups(block))) {
        const key = `${name}.${group}.*`;
        const paneId = `${name}:${group}`;
        const color = chart.srvColor(idx++);
        if (!srv.enabled.has(key)) { chart.hideServerOscillator(paneId); continue; }
        const pointsByCol = {};
        for (const col of cols) {
          pointsByCol[col] = block.points
            .filter(p => p[col] != null && isFinite(p[col]) && candleTimes.has(p[tk]))
            .map(p => ({ time: p[tk], value: p[col] }));
        }
        chart.showServerOscillator(paneId, cols, pointsByCol, color);
      }
    }
  }
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

function select(sym) { current = sym; $("empty").style.display = "none"; loadChart(); loadMeta(); loadSrvIndicators(); updateStar(); setCockpitSymbol(sym); }

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
// The intraday bar toggle (5m/1m) only applies to 1D; hide it elsewhere so it
// doesn't imply it affects daily/weekly timeframes.
function syncBarsUI() {
  $("bars").style.display = (tf === "1D") ? "" : "none";
  document.querySelectorAll("#bars button").forEach(b => b.classList.toggle("on", b.dataset.b === bar));
}
$("tfs").onclick = e => {
  if (e.target.tagName !== "BUTTON") return;
  tf = e.target.dataset.t;
  document.querySelectorAll("#tfs button").forEach(b => b.classList.toggle("on", b === e.target));
  $("tftag").textContent = tf;
  syncBarsUI();
  if (current) { loadChart(); loadSrvIndicators(); }
};
$("bars").onclick = e => {
  if (e.target.tagName !== "BUTTON") return;
  bar = e.target.dataset.b;
  localStorage.setItem("nse_intraday_bar", bar);
  document.querySelectorAll("#bars button").forEach(b => b.classList.toggle("on", b === e.target));
  if (current && tf === "1D") { loadChart(); loadSrvIndicators(); }
};
// ---- as-of date (point-in-time verification) ----
// Picking a date anchors the chart + server-indicator overlays to that IST day
// (composes with the timeframe: e.g. 1D shows that day, 1W the week ending then).
// Clearing returns to the live/latest view.
function setAsof(d) {
  asof = d || null;
  $("asof").value = asof || "";
  $("asofClear").style.display = asof ? "" : "none";
  $("asofWrap").classList.toggle("active", !!asof);
  if (current) { loadChart(); loadSrvIndicators(); loadMeta(); }
}
$("asof").onchange = e => setAsof(e.target.value);
$("asofClear").onclick = () => setAsof(null);

$("termBtn").onclick = () => {
  const on = document.body.classList.toggle("terminal");
  $("termBtn").classList.toggle("on", on);
  requestAnimationFrame(() => chart.fit());
};

$("fundaHead").onclick = () => $("funda").classList.toggle("collapsed");

// ---- compose ----
new SearchBox({ inputId: "search", dropdownId: "dropdown", onSelect: select, inWatchlist: s => wl.has(s) });
initThemeToggle("themeBtn", () => chart.themeAll());
chart.themeAll();  // sync chart to the (possibly persisted) theme on load
syncBarsUI();      // reflect the persisted bar size and 1D-only visibility

initCockpit();
// Deep link (?symbol=X) wins; otherwise open the most-traded stock.
(async () => {
  const want = new URLSearchParams(location.search).get("symbol");
  if (want) { select(want.toUpperCase()); return; }
  try { const r = await Api.search(""); if (r.results && r.results.length) select(r.results[0].symbol); } catch (e) {}
})();
// Live refresh of the open stock (price + chart) while the feed updates.
// Suspended while viewing a past date (as-of) so the view stays put.
setInterval(() => { if (current && !asof) { loadMeta(); loadChart(); } }, 60000);
