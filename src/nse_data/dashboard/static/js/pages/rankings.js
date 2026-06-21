// Weekly Rankings — browse each week's positional ranking (validated lean + slow-data overlay,
// ATR-sized basket). Pick a week, toggle basket-only vs all candidates.

const $ = id => document.getElementById(id);
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();
const f1 = n => n == null ? "—" : (+n).toFixed(1);
const f2 = n => n == null ? "—" : (+n).toFixed(2);
const inr = n => n == null ? "—" : (+n).toLocaleString("en-IN", { maximumFractionDigits: 0 });

const GOOD = new Set(["FII↑", "deliv↑", "pledge↓", "block-buy"]);
const BAD = new Set(["FII↓", "deliv↓", "pledge↑", "pledge-high↑", "downgrade", "junk-downgrade", "block-sell"]);
const state = { week: null, basketOnly: true };

async function init() {
  const r = await getJSON("/api/rankings/weeks");
  const weeks = r.weeks || [];
  if (!weeks.length) { $("meta").textContent = "no weekly ranking yet — first run is Monday 09:00 IST"; return; }
  $("weekPicker").innerHTML = weeks.map(w =>
    `<option value="${w.week_date}">${w.week_date} · ${w.selected}/${w.candidates}</option>`).join("");
  $("weekPicker").onchange = () => loadWeek($("weekPicker").value);
  $("viewBasket").onclick = () => { state.basketOnly = true; sync(); loadWeek(state.week); };
  $("viewAll").onclick = () => { state.basketOnly = false; sync(); loadWeek(state.week); };
  await loadWeek(weeks[0].week_date);
}

function sync() {
  $("viewBasket").classList.toggle("on", state.basketOnly);
  $("viewAll").classList.toggle("on", !state.basketOnly);
}

function flagHtml(flags) {
  return (flags || []).map(f =>
    `<span class="flag ${GOOD.has(f) ? "good" : BAD.has(f) ? "bad" : ""}">${f}</span>`).join("") || "—";
}

async function loadWeek(week) {
  state.week = week;
  const r = await getJSON(`/api/rankings/week/${week}?selected=${state.basketOnly}`);
  const rows = r.rows || [];
  const basket = rows.filter(x => x.selected);
  const totRisk = basket.reduce((s, x) => s + (x.risk_rupees || 0), 0);
  $("meta").textContent = `week of ${week}`;
  $("summary").textContent = `${basket.length} names sized · total risk ₹${inr(totRisk)}` +
    (state.basketOnly ? "" : ` · ${rows.length} candidates shown`);
  $("rankBody").innerHTML = rows.map(x => {
    const cls = x.selected ? "basket" : x.excluded ? "excluded" : "";
    const rank = x.selected ? x.final_rank : x.base_rank;
    const pd = x.pledge_delta, fd = x.fii_delta;
    return `<tr class="${cls}"><td class="num">${rank ?? "—"}</td><td>${x.symbol}</td>` +
      `<td>${x.sector || "—"}</td><td class="num">${f1(x.lean_score)}</td>` +
      `<td class="num">${f1(x.adj_score)}</td><td>${flagHtml(x.flags)}</td>` +
      `<td class="num ${pd > 0 ? "neg" : pd < 0 ? "pos" : ""}">${pd == null ? "—" : (pd > 0 ? "+" : "") + f2(pd)}</td>` +
      `<td class="num ${fd > 0 ? "pos" : fd < 0 ? "neg" : ""}">${fd == null ? "—" : (fd > 0 ? "+" : "") + f2(fd)}</td>` +
      `<td class="num">${x.delivery_conviction == null ? "—" : f2(x.delivery_conviction)}</td>` +
      `<td class="num">${f1(x.entry_px)}</td><td class="num">${f1(x.stop_px)}</td>` +
      `<td class="num">${x.qty ?? "—"}</td><td class="num">${inr(x.risk_rupees)}</td></tr>`;
  }).join("");
}

sync();
init();
