// Conviction — the persisted 13-stage synthesis (market bias + per-stock scored watchlist).
const $ = id => document.getElementById(id);
const getJSON = async u => (await fetch(u, { cache: "no-store" })).json();
const c = (v) => v == null ? "" : v;   // DATA_GAP → blank cell

async function init() {
  const r = await getJSON("/api/conviction");
  if (r.detail || !r.rows) { $("meta").textContent = r.detail || "no conviction snapshot yet — runs 08:45 IST"; return; }
  $("meta").textContent = `${r.date} · ${r.count} F&O names`;
  const m = r.macro || {};
  if (m.status === "ok") {
    $("macro").innerHTML =
      `<b>Market bias: ${m.regime}</b> · gap-bias <b>${m.gap_bias}</b> (GIFT ${m.gift_gap_pct}%) · ` +
      `US S&P ${m.us_spx_pct}% · US VIX ${m.us_vix} (${m.us_vix_pct}%) · India VIX ${m.india_vix} · ` +
      `Nifty ${m.nifty_last} (${m.nifty_pct}%) · Smart-money ${r.smart_money}`;
  } else {
    $("macro").innerHTML = `Macro regime: <b>DATA_GAP</b> — ${m.note || "global macro collector not active"}`;
  }
  const aplus = r.rows.filter(x => x.tier === "A+").length;
  $("bias").textContent = `A+ candidates: ${aplus} (zero gaps in catalyst/positioning/options + composite ≥7.5 required)`;
  const tcls = t => (t === "A+" ? "Ap" : (t || "").charAt(0));
  const n = v => v == null ? "—" : v;
  const dir = d => d === "LONG" ? '<span style="color:#16a34a;font-weight:700">LONG</span>'
    : d === "SHORT" ? '<span style="color:#dc2626;font-weight:700">SHORT</span>' : c(d);
  $("body").innerHTML = r.rows.map((x, i) =>
    `<tr><td class="num">${i + 1}</td><td><a href="/research?sym=${x.symbol}" style="color:#60a5fa;text-decoration:none">${x.symbol}</a></td>` +
    `<td class="num"><b>${x.composite}</b></td><td><span class="tier ${tcls(x.tier)}">${(x.tier || "").split(" ")[0]}</span></td>` +
    `<td>${dir(x.direction)}</td><td style="font-size:12px">${c(x.setup)}</td>` +
    `<td class="num">${n(x.entry)}</td><td class="num" style="color:#dc2626">${n(x.stop)}</td>` +
    `<td class="num">${n(x.t1)}</td><td class="num" style="color:#16a34a">${n(x.t2)}</td><td class="num">${n(x.t3)}</td>` +
    `<td class="num"><b>${x.rr == null ? "—" : "1:" + x.rr}</b></td><td class="num">${n(x.probability)}%</td>` +
    `<td class="num">${n(x.stages?.vol_expansion?.atr_pct)}</td>` +
    `<td class="gap">${(x.data_gaps || "")}</td></tr>`).join("");
}
init();
