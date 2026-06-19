// The stock cockpit — overview strip + lazy tabs (results / events / filings /
// activity / flow), all read from /api/stocks/{symbol}/*. Owned by stocks.js:
// it calls setCockpitSymbol() on every symbol switch; tab state deep-links via
// the ?tab= URL param. Every section renders an explicit empty-state, so the
// page is honest on a fresh DB.
import { $ } from "../core/util.js";

const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = v => v == null ? "—" : (+v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const cr = v => v == null ? "—" : "₹" + num(v) + " cr";
const pct = v => {
  if (v == null || isNaN(v)) return "—";
  const cls = v > 0 ? "up" : v < 0 ? "dn" : "";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${(+v).toFixed(1)}%</span>`;
};
const dt = s => esc(String(s ?? "—").slice(0, 16));
const empty = msg => `<div class="cempty">${esc(msg)}</div>`;

let symbol = null;
let tab = "results";
const cache = new Map();              // `${symbol}/${tab}` -> payload

async function api(section) {
  const key = `${symbol}/${section}`;
  if (cache.has(key)) return cache.get(key);
  const r = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/${section}`,
                        { cache: "no-store" });
  if (!r.ok) throw new Error(`${section}: HTTP ${r.status}`);
  const body = await r.json();
  cache.set(key, body);
  return body;
}

// ---- overview strip ----------------------------------------------------------

function chip(label, value, cls = "") {
  return `<span class="ochip ${cls}"><span class="k">${esc(label)}</span>${value}</span>`;
}

function renderOverview(o) {
  const parts = [];
  if (o.quality?.quality_score != null)
    parts.push(chip("Quality", num(o.quality.quality_score)));
  if (o.quality?.trend_regime)
    parts.push(chip("Trend", esc(o.quality.trend_regime)));
  const s = o.sector || {};
  if (s.index)
    parts.push(chip("Sector", `${esc(s.index)}${s.rs_rank != null ? ` · RS #${s.rs_rank}` : ""}` +
                              `${s.rs_trend ? ` (${esc(s.rs_trend)})` : ""}`));
  if (s.sector_class && s.sector_class !== "unknown")
    parts.push(chip("Engine", esc(s.sector_class)));
  const v = o.result_verdict;
  if (v) {
    const cls = v.direction === "short" ? "bad" : v.direction === "long" ? "good" : "";
    const icon = v.direction === "short" ? "🔻" : v.direction === "long" ? "🟢" : "▫️";
    parts.push(chip("Last result", `${icon} ${esc(v.label)}`, cls));
  }
  for (const sv of o.surveillance || [])
    parts.push(chip(sv.list, `stage ${esc(sv.stage)}`, "bad"));
  if (o.price_band?.band && !/no band/i.test(o.price_band.band))
    parts.push(chip("Band", esc(o.price_band.band) + "%"));
  if (o.next_event)
    parts.push(chip("Next result", `${dt(o.next_event.expected_date)} (${esc(o.next_event.confidence)})`));
  if ((o.consensus_sources || []).length)
    parts.push(chip("Estimates", esc(o.consensus_sources.join("+")), "good"));
  $("ovwStrip").innerHTML = parts.join("") ||
    `<span class="cempty">no intelligence recorded for this symbol yet</span>`;
}

// Ranking-engine composite badge — prepended to the overview strip once the
// score-history endpoint answers (separate fetch so a missing feature store
// just means no chip, never a broken overview).
async function renderScoreBadge() {
  let body;
  try {
    const r = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/score-history?days=5`,
                          { cache: "no-store" });
    if (!r.ok) return;
    body = await r.json();
  } catch (e) { return; }
  const l = body.latest;
  // operational score = lean (Valuation+Surprise, OOS-validated); fall back to buy/composite
  const v = l == null ? null : (l.lean_score ?? l.buy_score ?? l.composite);
  if (v == null) return;
  const cls = v >= 60 ? "good" : v < 45 ? "bad" : "";
  const rank = (l.sector_rank != null && l.sector_n != null)
    ? ` · #${l.sector_rank}/${l.sector_n} ${esc(l.sector || "")}` : "";
  const badge = chip("Lean", `<b>${(+v).toFixed(0)}</b>${rank}`, cls);
  const strip = $("ovwStrip");
  // prepend; if the strip is showing only the empty-state, replace it
  if (strip.querySelector(".cempty")) strip.innerHTML = badge;
  else strip.insertAdjacentHTML("afterbegin", badge);
}

// Integrated Buy Decision verdict — the headline chip (regime-adaptive Buy Score +
// verdict + classification), prepended to the overview strip. Gates on Trend, so a
// cheap/quality value-trap reads as AVOID, not a bare high score.
async function renderBuyCard() {
  let c;
  try {
    const r = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/buy-card`, { cache: "no-store" });
    if (!r.ok) return;
    c = await r.json();
  } catch (e) { return; }
  if (!c || !c.available || c.buy_score == null) return;
  const v = String(c.verdict || "");
  const cls = /^(STRONG BUY|BUY)/.test(v) ? "good" : /^(AVOID|EXIT|REDUCE)/.test(v) ? "bad" : "";
  const verb = v.split("—")[0].trim();                       // drop the "— reason" tail
  const vel = c.velocity == null ? "" :
    ` ${c.velocity > 0 ? "↑" : c.velocity < 0 ? "↓" : "→"}${Math.abs(c.velocity).toFixed(0)}`;
  const title = `Buy ${c.buy_score} · ${esc(c.classification || "")}`
    + (c.drivers_negative?.length ? ` · risks: ${esc(c.drivers_negative.join(", "))}` : "");
  const badge = `<span class="ochip ${cls}" title="${title}"><span class="k">Verdict</span>`
    + `<b>${esc(verb)}</b> ${(+c.buy_score).toFixed(0)}${vel}</span>`;
  const strip = $("ovwStrip");
  if (strip.querySelector(".cempty")) strip.innerHTML = badge;
  else strip.insertAdjacentHTML("afterbegin", badge);
}

// ---- tab renderers -------------------------------------------------------------

function table(headers, rows) {
  if (!rows.length) return "";
  return `<table class="ctable"><thead><tr>${
    headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => `<tr>${r.map(c => `<td>${c ?? "—"}</td>`).join("")}</tr>`).join("")
  }</tbody></table>`;
}

function section(title, inner) {
  return inner ? `<h3>${esc(title)}</h3>${inner}` : "";
}

function renderResults(d) {
  const out = [];
  const v = d.verdict;
  if (v) {
    const icon = v.direction === "short" ? "🔻" : v.direction === "long" ? "🟢" : "▫️";
    const flags = (v.flags || []).map(f => `<span class="flag">${esc(f)}</span>`).join(" ");
    let narrative = "";
    const n = v.narrative || {};
    const bits = [];
    if (n.guidance) bits.push(`Guidance ${esc(n.guidance)}`);
    if (n.fda_status) bits.push(`USFDA: ${esc(n.fda_status).replace("_", " ")}`);
    if (n.volume_growth != null) bits.push(`volumes ${pct(n.volume_growth)}`);
    if (n.order_inflow != null) bits.push(`order inflow ${cr(n.order_inflow)}`);
    if (n.dividend != null) bits.push(`dividend ₹${num(n.dividend)}/sh`);
    if (n.cc_revenue_growth_pct != null) bits.push(`cc-rev ${pct(n.cc_revenue_growth_pct)}`);
    if (n.tcv_usd_mn != null) bits.push(`TCV $${num(n.tcv_usd_mn)} mn`);
    if (bits.length) narrative = `<div class="news">📰 ${bits.join(" · ")}</div>`;
    out.push(`<div class="vcard ${v.direction || ""}">
      <div class="vhead">${icon} ${esc(v.period_ending)} — ${esc(v.label)}
        ${v.direction ? `(${esc(v.direction.toUpperCase())} bias)` : ""}</div>
      <div class="vsum">${esc(v.summary)}</div>
      <div class="vflags">${flags}</div>${narrative}</div>`);
  }
  const bank = d.quarters.some(q => q.nii_cr != null || q.ppop_cr != null);
  const p1 = x => x != null ? num(x) + "%" : "—";   // a stored percentage (margin/ratio)
  const qtable = qs => table(
    bank
      ? ["Period", "NII", "PPOP", "PPOP YoY", "PPOP QoQ", "Provisions", "GNPA%", "NNPA%", "CET1%", "ROA%", "PAT", "PAT YoY", "PAT QoQ"]
      : ["Period", "Revenue", "Rev YoY", "Rev QoQ", "EBITDA", "EBITDA%", "EBITDA YoY", "EBITDA QoQ", "PAT", "PAT YoY", "PAT QoQ", "Def.tax", "EPS"],
    qs.map(q => bank
      ? [dt(q.period_ending), cr(q.nii_cr), cr(q.ppop_cr), pct(q.yoy_ppop_pct), pct(q.qoq_ppop_pct),
         cr(q.provisions_cr), p1(q.gnpa_pct), p1(q.nnpa_pct), p1(q.cet1_ratio), p1(q.roa_pct),
         cr(q.pat_cr), pct(q.yoy_pat_pct), pct(q.qoq_pat_pct)]
      : [dt(q.period_ending), cr(q.revenue_cr), pct(q.yoy_revenue_pct), pct(q.qoq_revenue_pct),
         cr(q.ebitda_cr), p1(q.ebitda_margin_pct), pct(q.yoy_ebitda_pct), pct(q.qoq_ebitda_pct),
         cr(q.pat_cr), pct(q.yoy_pat_pct), pct(q.qoq_pat_pct), cr(q.deferred_tax_cr), num(q.eps)]));
  const con = d.quarters.filter(q => q.scope === "consolidated");
  const std = d.quarters.filter(q => q.scope === "standalone");
  if (con.length || std.length) {
    // CSS-only Standalone/Consolidated toggle (separate boxes). Default to
    // consolidated for non-banks (the group view the market prices), but to
    // standalone for banks — their NPA/CET1/ROA + deep history live only in the
    // standalone filing (NSE publishes no older consolidated XBRL).
    const conFirst = con.length > 0 && (!bank || std.length === 0);
    out.push(`<h3>Quarterly extractions</h3>
      <div class="scopetog">
        <input type="radio" name="qscope" id="qs-con" ${conFirst ? "checked" : ""} ${con.length ? "" : "disabled"}>
        <label for="qs-con">Consolidated</label>
        <input type="radio" name="qscope" id="qs-std" ${conFirst ? "" : "checked"} ${std.length ? "" : "disabled"}>
        <label for="qs-std">Standalone</label>
        <div class="scopebox" data-scope="con">${qtable(con) || empty("no consolidated results")}</div>
        <div class="scopebox" data-scope="std">${qtable(std) || empty("no standalone results")}</div>
      </div>`);
  } else {
    out.push(section("Quarterly extractions", empty("no extracted results yet")));
  }
  const estRows = d.estimates.flatMap(p => p.rows.map(r =>
    [dt(p.period_ending), esc(r.source), cr(r.rev_est_cr), cr(r.pat_est_cr),
     num(r.eps_est), cr(r.nii_est_cr), r.nim_est_pct != null ? num(r.nim_est_pct) + "%" : "—"]));
  out.push(section("Consensus estimates",
    table(["Quarter", "Source", "Revenue", "PAT", "EPS", "NII", "NIM"], estRows) ||
    empty("no estimates stored — the nightly job fetches for upcoming reporters")));
  out.push(section("Rating actions", table(
    ["Date", "Agency", "Action", "From", "To", "Instrument"],
    d.ratings.map(r => [dt(r.broadcast_dt), esc(r.agency), esc(r.action),
                        esc(r.old_rating), esc(r.new_rating), esc(r.instrument_type)]))));
  return out.join("");
}

function renderEvents(d) {
  const out = [];
  const su = d.earnings_setup;
  if (su) {
    out.push(`<div class="vcard"><div class="vhead">Earnings setup — ${dt(su.event_date)}</div>
      <div class="vsum">run-up 5d ${pct(su.run_up_5d)} / 10d ${pct(su.run_up_10d)}
      (${esc(su.run_up_class ?? "—")}) · implied move ${su.implied_move_pct != null ? "±" + num(su.implied_move_pct) + "%" : "—"}
      · sector rank ${su.sector_rank ?? "—"} · expectation ${num(su.expectation_proxy_score)}
      ${su.bfsi_macro_risk ? ' · <span class="flag">NIM/treasury macro risk</span>' : ""}</div></div>`);
  }
  out.push(section("Expected events", table(
    ["Type", "Expected", "Confidence", "Status", "Purpose"],
    d.pending.map(e => [esc(e.event_type), dt(e.expected_date), esc(e.confidence),
                        esc(e.status), esc(e.purpose)]))
    || empty("no expected events")));
  out.push(section("Board meetings", table(
    ["Date", "Purpose"],
    d.board_meetings.map(m => [dt(m.meeting_date), esc(m.purpose || m.details)]))));
  out.push(section("Corporate actions", table(
    ["Subject", "Ex-date", "Record date"],
    d.corporate_actions.map(a => [esc(a.subject), dt(a.ex_date), dt(a.record_date)]))));
  return out.join("") || empty("no events recorded");
}

function renderFilings(d) {
  const senti = s => s == null ? "—"
    : `<span class="${+s > 0 ? "up" : +s < 0 ? "dn" : ""}">${esc(s)}</span>`;
  return section("Announcements", table(
    ["Filed", "Subject", "Priority", "Sentiment", "Pipeline", ""],
    d.announcements.map(a => [
      dt(a.broadcast_dt), esc(a.subject), esc(a.priority), senti(a.sentiment),
      esc(a.pdf_status),
      a.attachment_url ? `<a href="${esc(a.attachment_url)}" target="_blank" rel="noopener">📄</a>` : "—",
    ]))) || empty("no filings collected for this symbol");
}

function renderActivity(d) {
  const out = [];
  // Explainable Buy-Score backtest — every entry/exit with the reason it fired, so the
  // strategy's decisions on this stock can be assessed (Buy Score ≥70, sell-on-decline).
  const sb = d.signal_backtest || {}, ss = sb.summary || {};
  if (ss.trades)
    out.push(`<div class="vcard"><div class="vsum">Lean-score strategy backtest
      (Valuation+Surprise ≥70 · OOS-validated): <b>${ss.trades}</b> trades ·
      win <b>${num(ss.win_rate)}%</b> · avg net ${pct(ss.avg_net_pct)} ·
      total ${pct(ss.total_net_pct)} · avg hold <b>${ss.avg_hold_days}d</b></div></div>`);
  out.push(section("Strategy backtest — every entry, exit & why", table(
    ["Entry", "Exit", "Days", "In ₹", "Out ₹", "Net", "Why bought", "Why sold"],
    (sb.trades || []).map(t => [
      esc(t.entry_date),
      esc(t.exit_date) + (t.open ? ' <span class="up">· open</span>' : ""),
      t.holding_days, num(t.entry_px), num(t.exit_px), pct(t.net_pct),
      esc(t.entry_reason), esc(t.exit_reason),
    ])) || empty("the Buy Score never crossed the buy threshold here — the strategy never bought this stock in the backtest window")));
  out.push(section("Signals", table(
    ["Detected", "Type", "Dir", "Conf", "Price", "ret 1d", "T1/SL"],
    d.signals.map(s => [
      dt(s.detected_at), esc(s.signal_type), esc(s.direction), num(s.confidence),
      num(s.price), pct(s.ret_1d),
      s.hit_t1 ? '<span class="up">T1</span>' : s.hit_sl ? '<span class="dn">SL</span>' : "—",
    ])) || empty("no signals fired for this symbol")));
  out.push(section("Paper trades", table(
    ["Entry", "Type", "Dir", "Entry ₹", "Exit ₹", "Reason", "Net P&L"],
    d.paper_trades.map(t => [
      dt(t.entry_time), esc(t.signal_type), esc(t.direction), num(t.entry_price),
      num(t.exit_price), esc(t.exit_reason ?? t.status),
      `<span class="${(t.net_pnl ?? 0) >= 0 ? "up" : "dn"}">${num(t.net_pnl)}</span>`,
    ]))));
  return out.join("");
}

function renderFlow(d) {
  const out = [];
  const cards = [];
  if (d.oi) cards.push(`OI ${num(d.oi.latest_oi)} (${pct(d.oi.avg_oi_pct)} vs avg)`);
  if (d.volatility) cards.push(`daily vol ${num(d.volatility.daily_volatility)} ·
    annualised ${num(d.volatility.annualised_volatility)}`);
  if (cards.length) out.push(`<div class="vcard"><div class="vsum">${cards.join(" &nbsp;·&nbsp; ")}</div></div>`);
  out.push(section("Delivery trend", table(
    ["Date", "Delivery %", "5d avg", "Trend", "Conviction"],
    d.delivery.slice(0, 10).map(r => [
      dt(r.session_date), num(r.delivery_ratio), num(r.delivery_ratio_5d_avg),
      esc(r.delivery_trend), num(r.delivery_conviction_score),
    ])) || empty("no delivery data")));
  out.push(section("Bulk / block deals", table(
    ["Date", "Type", "Client", "Side", "Qty", "Avg ₹"],
    d.large_deals.map(x => [dt(x.deal_date), esc(x.deal_type), esc(x.client_name),
                            esc(x.buy_sell), num(x.quantity), num(x.weighted_avg_price)]))));
  out.push(section("Insider trading", table(
    ["Intimated", "Acquirer", "Category", "Type", "Qty", "Value ₹"],
    d.insider.map(x => [dt(x.intimation_date), esc(x.acquirer_name), esc(x.acquirer_category),
                        esc(x.transaction_type), num(x.no_of_securities), num(x.value_in_rupees)]))));
  out.push(section("Shareholding", table(
    ["Quarter", "Promoter %", "Public %"],
    d.shareholding.map(x => [dt(x.qe_date), num(x.promoter_pct), num(x.public_pct)]))));
  return out.join("");
}

function renderMoves(d) {
  const out = [];
  if (d.grade)
    out.push(`<div class="vcard"><div class="vsum">Universe grade: <b>${esc(d.grade)}</b>
      &nbsp;·&nbsp; ≥2% intraday moves from the open (overnight gap excluded),
      <b>constant rises/falls first</b></div></div>`);
  const candList = cands => `<div class="mcand">${cands.map(c => {
    const t = `<span class="flag">${esc(c.source)}</span> <span class="dim">${esc(c.event_date || "")}</span> ${esc(c.summary)}`;
    return `<div class="mcand-row">${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">${t}</a>` : t}</div>`;
  }).join("")}</div>`;
  const cause = m => {
    const cands = m.candidates || [];
    const sysTag = m.cause_regime === "systematic"
      ? `<span class="flag" title="moved with the market/sector — not stock-specific">📉 systematic</span> ` : "";
    const tag = sysTag + (m.cause_category ? `<span class="flag">${esc(m.cause_category)}</span> ` : "");
    const when = m.cause_date ? `<span class="dim">(${esc(m.cause_date)})</span> ` : "";
    const txt = m.cause_summary ? esc(m.cause_summary) : "—";
    const primary = (m.cause_summary && m.cause_url)
      ? `<a href="${esc(m.cause_url)}" target="_blank" rel="noopener">${txt}</a>` : txt;
    const head = tag + when + primary;
    if (!cands.length) return head;
    // expandable: primary in the summary, all candidate sources on expand
    return `<details class="mcause"><summary>${head} <span class="dim">· ${cands.length} source${cands.length > 1 ? "s" : ""}</span></summary>${candList(cands)}</details>`;
  };
  const rows = (d.moves || []).map(m => {
    const cls = m.move_pct > 0 ? "up" : "dn";
    const mv = `<span class="${cls}">${m.move_pct > 0 ? "+" : ""}${(+m.move_pct).toFixed(2)}%</span>`;
    const dir = m.direction === "up" ? "🟢" : "🔻";
    return [
      dt(m.date),
      `${dir} <span class="flag">${esc(m.pattern)}</span>`,
      mv,
      `${esc(m.move_start)}–${esc(m.move_end)}`,
      m.leg_minutes != null ? Math.round(m.leg_minutes) + "m" : "—",
      pct(m.net_pct),
      m.consistency != null ? (+m.consistency).toFixed(2) : "—",
      cause(m),
    ];
  });
  out.push(section(
    "Intraday moves",
    table(["Date", "Pattern", "Move", "Window", "Dur", "Net", "Consistency", "Why (cause)"], rows)
    || empty("no significant intraday moves recorded (needs 1-min candles for this symbol)")));
  return out.join("");
}

const RENDER = { results: renderResults, events: renderEvents, filings: renderFilings,
                 activity: renderActivity, flow: renderFlow, moves: renderMoves };

// ---- wiring -----------------------------------------------------------------

async function renderTab() {
  if (!symbol) return;
  const panel = $("cpanel");
  panel.innerHTML = `<div class="cempty">loading…</div>`;
  try {
    const data = await api(tab);
    panel.innerHTML = RENDER[tab](data) || empty("nothing here yet");
  } catch (e) {
    panel.innerHTML = empty(String(e));
  }
}

function setTab(t, { push = true } = {}) {
  tab = t;
  for (const b of document.querySelectorAll("#ctabs button"))
    b.classList.toggle("on", b.dataset.tab === t);
  if (push) {
    const u = new URL(location.href);
    u.searchParams.set("tab", t);
    history.replaceState(null, "", u);
  }
  renderTab();
}

export function setCockpitSymbol(sym) {
  symbol = sym;
  cache.clear();
  $("cockpit").style.display = "";
  const u = new URL(location.href);
  u.searchParams.set("symbol", sym);
  history.replaceState(null, "", u);
  api("overview").then(renderOverview).catch(() => { $("ovwStrip").innerHTML = ""; })
    .finally(() => renderScoreBadge().finally(renderBuyCard));   // verdict chip ends up leftmost
  renderTab();
}

export function initCockpit() {
  document.querySelectorAll("#ctabs button").forEach(b =>
    b.onclick = () => setTab(b.dataset.tab));
  const want = new URLSearchParams(location.search).get("tab");
  if (want && RENDER[want]) setTab(want, { push: false });
}
