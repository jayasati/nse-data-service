// Intraday conviction — live momentum scanner ranked on indicator_live. Discretionary, not an edge.
const $ = id => document.getElementById(id);
const getJSON = async u => (await fetch(u, { cache: "no-store" })).json();
const n = v => v == null ? "—" : v;
const dir = d => d === "LONG" ? '<span style="color:#16a34a;font-weight:700">LONG</span>'
  : d === "SHORT" ? '<span style="color:#dc2626;font-weight:700">SHORT</span>' : "—";
const orbCell = o => o === 1 ? '<span class="up">break ▲</span>' : o === -1 ? '<span class="down">break ▼</span>' : '<span class="flat">inside</span>';
const stCell = s => s === 1 ? '<span class="up">▲</span>' : s === -1 ? '<span class="down">▼</span>' : '<span class="flat">~</span>';
const vwapCell = (p, d) => p == null ? "—" : `<span style="color:${p >= 0 ? '#16a34a' : '#dc2626'}">${p > 0 ? '+' : ''}${p}%</span>`;
const gammaCell = g => g === "amplifying" ? '<span class="pill" style="color:#dc2626">amp</span>'
  : g === "pinning" ? '<span class="pill" style="color:#60a5fa">pin</span>' : "";
const convCell = c => {
  const col = c >= 8 ? "#16a34a" : c >= 6.5 ? "#f59e0b" : "#9aa";
  return `<b style="color:${col}">${c}</b>`;
};

async function init() {
  const r = await getJSON("/api/intraday-conviction");
  if (!r.rows) { $("meta").textContent = r.detail || "no live intraday data"; return; }
  const mk = r.market_open ? "LIVE" : "market closed (last snapshot)";
  $("meta").textContent = `${mk} · ${r.count} setups (${r.longs}L / ${r.shorts}S) · refreshed ${r.as_of}`;
  $("body").innerHTML = r.rows.map((x, i) =>
    `<tr><td class="num">${i + 1}</td>` +
    `<td><a href="/research?sym=${x.symbol}" style="color:#60a5fa;text-decoration:none">${x.symbol}</a> ` +
    `<a href="/intraday?sym=${x.symbol}" title="live VWAP/ORB chart" style="color:#16a34a;text-decoration:none;font-size:11px">▸live</a></td>` +
    `<td class="num">${convCell(x.conviction)}</td><td>${dir(x.direction)}</td>` +
    `<td class="num">${n(x.ltp)}</td><td class="num">${vwapCell(x.vs_vwap_pct)}</td>` +
    `<td>${orbCell(x.orb)}</td><td>${stCell(x.st_5m)}</td><td class="num">${n(x.rsi_5m)}</td>` +
    `<td class="num">${n(x.rvol)}×</td><td>${gammaCell(x.gamma)}</td>` +
    `<td class="num">${n(x.entry)}</td><td class="num" style="color:#dc2626">${n(x.stop)}</td>` +
    `<td class="num">${n(x.t1)}</td><td class="num" style="color:#16a34a">${n(x.t2)}</td><td class="num">${n(x.t3)}</td></tr>`
  ).join("");
}
init();
setInterval(init, 30_000);   // 30s auto-refresh (server recomputes from per-minute indicator_live)
