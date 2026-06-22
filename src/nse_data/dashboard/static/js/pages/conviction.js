// Conviction — the persisted multi-factor synthesis (market bias + per-stock scored watchlist).
const $ = id => document.getElementById(id);
const getJSON = async u => (await fetch(u, { cache: "no-store" })).json();
const c = (v) => v == null ? "" : v;   // DATA_GAP → blank cell
let ROWS = [];
let TF = "1D";
let HZ = "swing";   // target horizon: swing (1.2/2/3 ATR) or intraday (0.6/1.2/1.8 ATR)
const lv = (x, k) => HZ === "intraday" ? x["intraday_" + k] : x[k];

const tcls = t => (t === "A+" ? "Ap" : (t || "").charAt(0));
const n = v => v == null ? "—" : v;
const dir = d => d === "LONG" ? '<span style="color:#16a34a;font-weight:700">LONG</span>'
  : d === "SHORT" ? '<span style="color:#dc2626;font-weight:700">SHORT</span>' : c(d);
const confl = x => {
  const lb = x.conf_label, col = lb === "ALIGNED" ? "#16a34a" : lb === "CONTRADICTED" ? "#dc2626" : "#9aa";
  if (!lb) return "—";
  const ag = x.conf_agreement == null ? "" : ` ${x.conf_agreement > 0 ? "+" : ""}${x.conf_agreement}`;
  const against = x.conf_against ? ` <span style="opacity:.6;font-size:10px">vs ${x.conf_against}</span>` : "";
  return `<span style="color:${col};font-weight:600;font-size:11px">${lb}${ag}</span>${against}`;
};

// Per-timeframe structure read (data already computed by the MTF engine, in stages.structure).
const arrow = t => t > 0 ? '<span class="up">▲ up</span>' : t < 0 ? '<span class="down">▼ down</span>'
  : '<span class="flat">~ range</span>';
function trendCell(x) {
  const st = (x.stages && x.stages.structure) || {};
  if (TF === "1H") return arrow(st.h1_trend);
  if (TF === "5M") {
    const m = st.m5_trend, note = st.m5_note || "";
    const lbl = m > 0 ? '<span class="up">▲</span>' : m < 0 ? '<span class="down">▼</span>' : '<span class="flat">~</span>';
    // opening window = gap-adjusted opening-drive (scored); mid-session = swing-BOS. note explains which.
    const txt = note || (m ? "BOS" : "no break");
    return `${lbl} <span style="font-size:10px;opacity:.8">${txt}</span> <a href="/intraday?sym=${x.symbol}" title="live 5M / ORB / VWAP" style="color:#16a34a;text-decoration:none;font-size:10px">▸live</a>`;
  }
  const sw = st.last_sweep || "", pos = st.range_pos_pct;          // 1D: daily structure
  const d = sw.includes("bull") ? '<span class="up">▲</span>' : sw.includes("bear") ? '<span class="down">▼</span>'
    : '<span class="flat">~</span>';
  return `${d} <span style="font-size:11px;opacity:.65">${pos != null ? pos + "% rng" : ""}</span>`;
}

function renderBody() {
  $("body").innerHTML = ROWS.map((x, i) =>
    `<tr><td class="num">${i + 1}</td><td><a href="/research?sym=${x.symbol}" style="color:#60a5fa;text-decoration:none">${x.symbol}</a>` +
    ` <a href="/intraday?sym=${x.symbol}" title="watch live 5M/ORB/VWAP for entry timing" style="color:#16a34a;text-decoration:none;font-size:11px">▸live</a></td>` +
    `<td class="num"><b>${x.conviction_adj ?? x.composite}</b></td><td><span class="tier ${tcls(x.tier)}">${(x.tier || "").split(" ")[0]}</span></td>` +
    `<td>${dir(x.direction)}</td><td>${trendCell(x)}</td><td>${confl(x)}</td><td style="font-size:12px">${c(x.setup)}</td>` +
    `<td class="num">${n(x.entry)}</td>` +
    `<td class="num">${x.open_iep == null ? "—" : x.open_iep}${x.gap_pct == null ? "" : ` <span style="font-size:10px;color:${x.gap_pct >= 0 ? "#16a34a" : "#dc2626"}">${x.gap_pct > 0 ? "+" : ""}${x.gap_pct}%</span>`}</td>` +
    `<td class="num" style="color:#dc2626">${n(lv(x, "stop"))}</td>` +
    `<td class="num">${n(lv(x, "t1"))}</td><td class="num" style="color:#16a34a">${n(lv(x, "t2"))}</td><td class="num">${n(lv(x, "t3"))}</td>` +
    `<td class="num"><b>${lv(x, "rr") == null ? "—" : "1:" + lv(x, "rr")}</b></td><td class="num">${n(x.probability)}%</td>` +
    `<td class="num">${n(x.stages?.vol_expansion?.atr_pct)}</td>` +
    `<td class="gap">${(x.data_gaps || "")}</td></tr>`).join("");
}

async function init() {
  const r = await getJSON("/api/conviction");
  if (r.detail || !r.rows) { $("meta").textContent = r.detail || "no conviction snapshot yet — runs 09:10 IST (post pre-open)"; return; }
  $("meta").textContent = `${r.date} · ${r.count} F&O names · refreshed ${new Date().toLocaleTimeString()}`;
  const m = r.macro || {};
  if (m.status === "ok") {
    const gapnum = m.preopen_gap_pct != null ? `${m.preopen_gap_pct}%` : m.gift_gap_pct != null ? `${m.gift_gap_pct}%` : "—";
    const pd = m.participant_divergence;
    const divLine = (pd && pd.status === "ok")
      ? `<div style="margin-top:4px;font-size:12px"><b>Participant OI:</b> FII <b style="color:${pd.fii_long_pct >= 50 ? "#16a34a" : "#dc2626"}">${pd.fii_long_pct}% long</b> · Retail <b style="color:${pd.client_long_pct >= 50 ? "#16a34a" : "#dc2626"}">${pd.client_long_pct}% long</b> · DII ${pd.dii_long_pct}% · Pro ${pd.pro_long_pct}% → <b>${pd.read}</b></div>`
      : "";
    $("macro").innerHTML =
      `<b>Market bias: ${m.regime}</b> · gap <b>${m.gap_bias}</b> ${gapnum} <span style="opacity:.7">(src: ${m.gap_source})</span> · ` +
      `US S&P ${m.us_spx_pct}% · US VIX ${m.us_vix} (${m.us_vix_pct}%) · India VIX ${m.india_vix} · ` +
      `Nifty ${m.nifty_last} (${m.nifty_pct}%) · Smart-money ${r.smart_money}` + divLine;
  } else {
    $("macro").innerHTML = `Macro regime: <b>DATA_GAP</b> — ${m.note || "global macro collector not active"}`;
  }
  const aplus = r.rows.filter(x => x.tier === "A+").length;
  $("bias").textContent = `A+ candidates: ${aplus} (zero gaps in catalyst/positioning/options + composite ≥7.5 required)`;
  ROWS = r.rows;
  renderBody();
}

document.querySelectorAll(".ctrl button[data-tf]").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ctrl button[data-tf]").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); TF = b.dataset.tf;
  $("tfHead").textContent = `Trend (${TF})`;
  renderBody();
});
document.querySelectorAll(".ctrl button[data-hz]").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ctrl button[data-hz]").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); HZ = b.dataset.hz;
  $("hzNote").innerHTML = HZ === "intraday"
    ? "— stop/T1/T2/T3 = <b>0.6/0.6/1.2/1.8×ATR</b> for a same-day exit (tighter, single-session)"
    : "— stop/T1/T2/T3 = <b>1.2/2/3×ATR</b> for a multi-day hold";
  renderBody();
});
init();
setInterval(init, 60_000);   // auto-refresh on screen; server re-runs the engine every 5 min intraday
