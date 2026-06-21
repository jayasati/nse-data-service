// Strategy Analytics — generic backtest study over /api/strategy-analytics/*.
// Pick a strategy + run → metric cards, cumulative-P&L curve, parametric P&L drill-down
// (stock/day/week/month/regime/direction/exit/trend), and the filterable trades table.

const $ = id => document.getElementById(id);
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();
const inr = n => n == null ? "—" : (+n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const f2 = n => n == null ? "—" : (+n).toFixed(2);
const dt = ts => ts == null ? "—" : String(ts).slice(0, 16).replace("T", " ");
const pnlCls = n => (+n) > 0 ? "pos" : (+n) < 0 ? "neg" : "";

const state = { strategy: null, runId: null, dim: "symbol", tfDir: "" };
let chart, series;

async function init() {
  const r = await getJSON("/api/strategy-analytics/strategies");
  const strats = r.strategies || [];
  if (!strats.length) { $("meta").textContent = "no backtests persisted yet"; return; }
  $("stratPicker").innerHTML = strats.map(s => `<option value="${s.strategy}">${s.strategy}</option>`).join("");
  $("stratPicker").onchange = () => loadRuns($("stratPicker").value);
  $("runPicker").onchange = () => selectRun(+$("runPicker").value);
  $("dimBar").querySelectorAll(".dimbtn").forEach(b => b.onclick = () => {
    $("dimBar").querySelectorAll(".dimbtn").forEach(x => x.classList.remove("on"));
    b.classList.add("on"); state.dim = b.dataset.dim; loadPnl();
  });
  document.querySelectorAll("[data-tf-dir]").forEach(b => b.onclick = () => {
    document.querySelectorAll("[data-tf-dir]").forEach(x => x.classList.remove("on"));
    b.classList.add("on"); state.tfDir = b.dataset.tfDir; loadTrades();
  });
  await loadRuns(strats[0].strategy);
}

async function loadRuns(strategy) {
  state.strategy = strategy;
  const r = await getJSON(`/api/strategy-analytics/runs?strategy=${strategy}&limit=50`);
  const runs = r.runs || [];
  $("runPicker").innerHTML = runs.map(x =>
    `<option value="${x.id}">#${x.id} · ${x.run_date} · ${x.n_trades} trades</option>`).join("");
  if (runs.length) await selectRun(runs[0].id);
}

async function selectRun(runId) {
  state.runId = runId;
  const run = await getJSON(`/api/strategy-analytics/runs/${runId}`);
  $("meta").textContent = `${run.strategy} · ${run.start_date}→${run.end_date} · ${run.n_symbols} symbols`;
  $("cTrades").textContent = run.n_trades;
  $("cWin").textContent = run.win_rate_pct == null ? "—" : run.win_rate_pct + "%";
  $("cPF").textContent = f2(run.profit_factor);
  $("cExp").textContent = run.expectancy == null ? "—" : (+run.expectancy).toFixed(3) + "%";
  // Honest money: the cumulative-curve endpoint = actual net ₹, and the rupee drawdown.
  // (net_pct is a per-trade %-sum — it does NOT equal capital return when position sizes vary.)
  const curve = run.equity_curve || [];
  const totalR = curve.length ? curve[curve.length - 1].cum_pnl : null;
  $("cNet").textContent = totalR == null ? "—" : "₹" + inr(totalR);
  $("cNet").className = "card-val " + pnlCls(totalR);
  $("cDD").textContent = run.max_dd_rupees == null ? "—" : "₹" + inr(run.max_dd_rupees);
  $("cDD").className = "card-val neg";
  $("cSharpe").textContent = f2(run.sharpe);
  $("cRR").textContent = f2(run.avg_rr);
  renderCurve(run.equity_curve || []);
  await Promise.all([loadPnl(), loadTrades()]);
}

function renderCurve(curve) {
  if (!chart) {
    chart = LightweightCharts.createChart($("curve"), {
      height: 220, layout: { background: { color: "transparent" }, textColor: "#9aa" },
      grid: { vertLines: { color: "#222" }, horzLines: { color: "#222" } },
      timeScale: { timeVisible: false } });
    series = chart.addAreaSeries({ lineColor: "#3b82f6", topColor: "rgba(59,130,246,.4)", bottomColor: "rgba(59,130,246,0)" });
    new ResizeObserver(() => chart.applyOptions({ width: $("curve").clientWidth })).observe($("curve"));
  }
  series.setData(curve.map(p => ({ time: Math.floor(new Date(p.ts).getTime() / 1000), value: p.cum_pnl })));
  chart.applyOptions({ width: $("curve").clientWidth });
  chart.timeScale().fitContent();
}

async function loadPnl() {
  const r = await getJSON(`/api/strategy-analytics/runs/${state.runId}/pnl?by=${state.dim}`);
  $("dimHead").textContent = state.dim;
  $("pnlBody").innerHTML = (r.buckets || []).map(b =>
    `<tr><td>${b.bucket}</td><td class="num">${b.trades}</td><td class="num">${b.wins}</td>` +
    `<td class="num">${b.losses}</td><td class="num">${b.win_rate ?? "—"}</td>` +
    `<td class="num ${pnlCls(b.pnl)}">${inr(b.pnl)}</td><td class="num">${f2(b.avg_rr)}</td></tr>`).join("");
}

async function loadTrades() {
  const q = state.tfDir ? `&direction=${state.tfDir}` : "";
  const r = await getJSON(`/api/strategy-analytics/runs/${state.runId}/trades?limit=2000${q}`);
  $("tradeFilter").textContent = `${r.count} trades`;
  $("tradesBody").innerHTML = (r.trades || []).map(t =>
    `<tr><td>${dt(t.entry_ts).slice(0, 10)}</td><td>${t.symbol}</td>` +
    `<td>${t.direction === "long" ? "🟢" : "🔴"}</td><td>${dt(t.entry_ts).slice(11)}</td>` +
    `<td class="num">${f2(t.entry_price)}</td><td>${dt(t.exit_ts).slice(11)}</td>` +
    `<td class="num">${f2(t.exit_price)}</td><td class="num">${f2(t.stop)}</td>` +
    `<td class="num">${f2(t.target)}</td><td>${t.exit_reason}</td>` +
    `<td class="num">${f2(t.rr_achieved)}</td><td class="num ${pnlCls(t.net_pnl)}">${inr(t.net_pnl)}</td></tr>`).join("");
}

init();
