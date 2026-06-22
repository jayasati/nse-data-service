// Intraday board — live VWAP / ORB / rvol read of the focused universe (auto-refreshes).
const $ = id => document.getElementById(id);
const getJSON = async u => (await fetch(u, { cache: "no-store" })).json();
let DATA = [], filter = "all";

const n = (v, d = 1) => v == null ? "—" : (typeof v === "number" ? v.toFixed(d) : v);
const biasCell = b => b === "LONG" ? '<td class="long">LONG</td>'
  : b === "SHORT" ? '<td class="short">SHORT</td>' : "<td>—</td>";

function render() {
  let rows = DATA;
  if (filter === "LONG" || filter === "SHORT") rows = rows.filter(x => x.bias === filter);
  else if (filter === "orb") rows = rows.filter(x => x.orb_state === "above" || x.orb_state === "below");
  $("body").innerHTML = rows.map((x, i) => {
    const vsv = x.vs_vwap_pct;
    const vsvCell = vsv == null ? "<td class='num'>—</td>"
      : `<td class="num ${vsv >= 0 ? "long" : "short"}">${vsv > 0 ? "+" : ""}${vsv}%</td>`;
    const orb = x.orb_state ? `<span class="pill ${x.orb_state}">${x.orb_state}</span>` : "—";
    const rvol = x.rvol_5m == null ? "—"
      : `<span class="${x.rvol_5m >= 1.5 ? "num hot" : ""}">${x.rvol_5m.toFixed(2)}</span>`;
    return `<tr><td class="num">${i + 1}</td>` +
      `<td><a href="/research?sym=${x.symbol}" style="color:#60a5fa;text-decoration:none">${x.symbol}</a></td>` +
      biasCell(x.bias) + `<td class="num">${n(x.ltp)}</td>` + vsvCell +
      `<td>${orb}</td><td class="num">${n(x.orb_high)}</td><td class="num">${n(x.orb_low)}</td>` +
      `<td class="num">${rvol}</td><td style="font-size:12px">${x.structure_5m ?? "—"}</td>` +
      `<td class="num">${n(x.rsi_5m, 0)}</td><td style="font-size:12px">${x.momentum_state ?? "—"}</td></tr>`;
  }).join("");
  $("meta").textContent = `${DATA.length} live names · ${rows.length} shown`;
}

async function load() {
  const r = await getJSON("/api/intraday");
  if (r.detail || !r.rows) { $("meta").textContent = r.detail || "no live snapshot (market closed?)"; return; }
  DATA = r.rows;
  const t = r.as_of ? new Date(r.as_of).toLocaleTimeString() : "?";
  render();
  $("meta").textContent += ` · updated ${t}`;
}

document.querySelectorAll(".ctrl button[data-f]").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ctrl button[data-f]").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); filter = b.dataset.f; render();
});
load();
setInterval(load, 30_000);
