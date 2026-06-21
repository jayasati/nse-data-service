// Stock Research — interactive per-symbol study: candles + news markers + delivery pane, click a
// day to see its intraday >1.5% legs and news; holding trend + block deals + news timeline below.

const $ = id => document.getElementById(id);
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();
const f1 = n => n == null ? "—" : (+n).toFixed(1);
const f2 = n => n == null ? "—" : (+n).toFixed(2);
const sgn = n => n == null ? "" : (n > 0 ? "+" : "");

let chart, candle, deliv, data, byDate = {}, newsByDate = {};

function ensureChart() {
  if (chart) return;
  chart = LightweightCharts.createChart($("chart"), {
    height: 430, layout: { background: { color: "transparent" }, textColor: "#9aa" },
    grid: { vertLines: { color: "#1f2430" }, horzLines: { color: "#1f2430" } },
    rightPriceScale: { scaleMargins: { top: 0.05, bottom: 0.28 } },
    timeScale: { timeVisible: false, borderColor: "#333" },
    crosshair: { mode: 0 },
  });
  candle = chart.addCandlestickSeries({ upColor: "#16a34a", downColor: "#dc2626",
    borderUpColor: "#16a34a", borderDownColor: "#dc2626", wickUpColor: "#16a34a", wickDownColor: "#dc2626" });
  deliv = chart.addHistogramSeries({ priceScaleId: "deliv", color: "#3b82f6" });
  chart.priceScale("deliv").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
  new ResizeObserver(() => chart.applyOptions({ width: $("chart").clientWidth })).observe($("chart"));
  chart.subscribeClick(p => { if (p && p.time) showDay(p.time); });
}

async function load() {
  const sym = $("sym").value.trim().toUpperCase();
  const r = await getJSON(`/api/research/${encodeURIComponent(sym)}?start=${$("start").value}&end=${$("end").value}`);
  if (r.detail || !r.bars) { $("meta").textContent = r.detail || "no data"; return; }
  data = r; byDate = {}; newsByDate = {};
  r.bars.forEach(b => byDate[b.time] = b);
  (r.intraday || []).forEach(d => byDate[d.date] && (byDate[d.date].legs = d.legs));
  (r.news.items || []).forEach(n => (newsByDate[n.date] = newsByDate[n.date] || []).push(n));
  const first = r.bars[0], last = r.bars[r.bars.length - 1];
  const net = ((last.close - first.close) / first.close * 100).toFixed(0);
  $("meta").textContent = `${r.symbol} · ${r.start}→${r.end} · ${first.close}→${last.close} (${sgn(net)}${net}%)`;
  ensureChart();
  candle.setData(r.bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
  deliv.setData(r.bars.map(b => ({ time: b.time, value: b.deliv_pct || 0,
    color: b.close >= b.open ? "rgba(22,163,74,.6)" : "rgba(220,38,38,.6)" })));
  deliv.createPriceLine({ price: 35, color: "#888", lineStyle: 2, lineWidth: 1, title: "35% conviction" });
  candle.setMarkers((r.news.markers || []).map(m => ({ time: m.time, position: "aboveBar",
    color: "#f59e0b", shape: "arrowDown", text: m.count > 1 ? `${m.count} news` : "news" })));
  chart.timeScale().fitContent();
  renderTables(r);
  showDay(last.time);
}

function showDay(time) {
  const b = byDate[time]; if (!b) return;
  const news = newsByDate[time] || [];
  const legs = (b.legs || []).map(l =>
    `<span class="leg ${l.dir.toLowerCase()}">${l.from}→${l.to} ${l.dir} ${sgn(l.pct)}${l.pct}%</span>`).join(" ") || "<span class='hint'>no >1.5% intraday leg</span>";
  const newsHtml = news.length ? news.map(n =>
    `<div class="nw"><a href="${n.url || "#"}" target="_blank">${n.headline}</a><div class="src">${n.source || ""}</div></div>`).join("")
    : "<div class='hint'>no news logged this day</div>";
  $("dayCard").innerHTML =
    `<h3>${time}</h3>` +
    `<div>O ${f1(b.open)} · H ${f1(b.high)} · L ${f1(b.low)} · C ${f1(b.close)}</div>` +
    `<div>gap <b class="${b.gap_pct > 0 ? "pos" : b.gap_pct < 0 ? "neg" : ""}">${b.gap_pct == null ? "—" : sgn(b.gap_pct) + b.gap_pct + "%"}</b> · ` +
    `day <b class="${b.day_pct > 0 ? "pos" : "neg"}">${sgn(b.day_pct)}${b.day_pct}%</b> · ` +
    `range ${b.range_pct}% · deliv ${b.deliv_pct == null ? "—" : f1(b.deliv_pct) + "%"}</div>` +
    `<div style="margin:8px 0">${legs}</div><hr style="border-color:#262b36">${newsHtml}`;
}

function renderTables(r) {
  $("holdBody").innerHTML = (r.holdings || []).map(h =>
    `<tr><td>${h.qe_date}</td><td class="num">${f2(h.promoter)}</td><td class="num">${f2(h.fii)}</td>` +
    `<td class="num">${f2(h.dii)}</td><td class="num">${h.pledge == null ? "—" : f2(h.pledge)}</td></tr>`).join("");
  $("dealBody").innerHTML = (r.deals || []).map(d =>
    `<tr><td>${d.date}</td><td>${d.type}</td><td class="${d.side === "BUY" ? "pos" : "neg"}">${d.side || "—"}</td>` +
    `<td class="num">${(d.qty || 0).toLocaleString("en-IN")}</td><td class="num">${f1(d.price)}</td>` +
    `<td>${(d.client || "").slice(0, 34)}</td></tr>`).join("") || "<tr><td colspan=6 class='hint'>none</td></tr>";
  const items = r.news.items || [];
  $("newsCount").textContent = `${items.length} items`;
  $("newsBody").innerHTML = items.slice().reverse().map(n =>
    `<tr><td>${n.date}</td><td><a href="${n.url || "#"}" target="_blank" style="color:#60a5fa;text-decoration:none">${n.headline}</a></td>` +
    `<td class="src">${n.source || ""}</td></tr>`).join("");
}

$("load").onclick = load;
load();
