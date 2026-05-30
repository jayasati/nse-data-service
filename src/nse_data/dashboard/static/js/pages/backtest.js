// Backtest results page. Loads list of runs, then on selection populates
// aggregate cards, equity curve, by-symbol table, and trades table.

const $ = id => document.getElementById(id);

const fmt = n => n == null ? "—" : (+n).toLocaleString("en-IN");
const fmt2 = n => n == null ? "—"
  : (+n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt0 = n => n == null ? "—" : (+n).toLocaleString("en-IN");

const tsToIst = ts => {
  if (ts == null) return "—";
  const d = new Date((ts + 19800) * 1000);
  // UTC parts of the shifted date == IST parts of the real date
  const pad = n => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
};

let state = {
  runs: [],
  currentRunId: null,
  pnlMode: "leveraged",     // "leveraged" | "raw"
  bySym: [],
  bySymSort: { col: "pnl_leveraged", dir: "desc" },
  symbolFilter: null,
  curveChart: null,
  curveSeries: null,
};

// ============================================================== fetchers
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();

async function loadRuns() {
  const r = await getJSON("/api/backtests/runs?limit=50");
  state.runs = r.runs || [];
  const picker = $("runPicker");
  picker.innerHTML = state.runs.length === 0
    ? `<option value="">No runs yet</option>`
    : state.runs.map(run => {
        const date = new Date(run.created_at * 1000).toISOString().slice(0, 16).replace("T", " ");
        return `<option value="${run.id}">#${run.id} · ${run.strategy} · ${date} · ${run.symbols_count} sym · ${run.total_trades} tr</option>`;
      }).join("");

  if (state.runs.length === 0) {
    $("meta").textContent = "Run the backtester to populate this page.";
    return;
  }

  const urlRun = new URL(location.href).searchParams.get("run");
  const initial = urlRun && state.runs.some(r => String(r.id) === urlRun)
    ? Number(urlRun) : state.runs[0].id;
  picker.value = String(initial);
  await selectRun(initial);
}

async function selectRun(runId) {
  state.currentRunId = runId;
  state.symbolFilter = null;
  $("tradeFilter").textContent = "";
  const url = new URL(location.href);
  url.searchParams.set("run", String(runId));
  history.replaceState(null, "", url);

  const [run, bySym, trades] = await Promise.all([
    getJSON(`/api/backtests/runs/${runId}`),
    getJSON(`/api/backtests/runs/${runId}/by-symbol`),
    getJSON(`/api/backtests/runs/${runId}/trades?limit=500`),
  ]);

  state.bySym = bySym.by_symbol || [];

  renderMeta(run);
  renderCards(run);
  renderCurve(run.equity_curve || []);
  renderBySym();
  renderTrades(trades.trades || []);
}

// ============================================================== renderers
function renderMeta(run) {
  const created = new Date(run.created_at * 1000).toISOString().replace("T", " ").slice(0, 16);
  $("meta").textContent = `${run.strategy} · ${run.universe} (${run.symbols_count}) · ${run.start_date} → ${run.end_date} · ${run.leverage}× · gap_mode=${run.params.gap_mode} · RR≥${run.params.rr_min} · created ${created}`;
}

function renderCards(run) {
  const leveraged = state.pnlMode === "leveraged";
  const pnl = leveraged ? run.pnl_leveraged : run.pnl_raw;
  const pnlClass = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";

  $("cSignals").textContent = fmt(run.total_signals);
  $("cTrades").textContent  = fmt(run.total_trades);
  $("cWinRate").textContent = run.total_trades ? `${run.win_rate.toFixed(1)}%` : "—";
  $("cWL").textContent      = `${run.wins} / ${run.losses}`;
  const pnlEl = $("cPnl");
  pnlEl.textContent = `₹ ${fmt2(pnl)}`;
  pnlEl.className = `card-val ${pnlClass}`;
  $("cDD").textContent      = `₹ ${fmt2(run.max_dd_raw)}`;
}

function renderCurve(points) {
  if (state.curveChart) { state.curveChart.remove(); state.curveChart = null; }
  const el = $("curve");
  el.innerHTML = "";
  if (!points.length) {
    el.innerHTML = `<div class="none" style="padding:80px">No trades</div>`;
    return;
  }
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { type: "solid", color: "transparent" },
              textColor: getCssVar("--txt2") || "#8a8a8a" },
    grid:   { vertLines: { color: getCssVar("--line") || "#2a2a2a" },
              horzLines: { color: getCssVar("--line") || "#2a2a2a" } },
    rightPriceScale: { borderColor: getCssVar("--line") || "#2a2a2a" },
    timeScale: { borderColor: getCssVar("--line") || "#2a2a2a", timeVisible: true },
    width: el.clientWidth,
    height: el.clientHeight,
  });
  const series = chart.addAreaSeries({
    lineColor: "#5b8def",
    topColor: "rgba(91, 141, 239, .35)",
    bottomColor: "rgba(91, 141, 239, 0)",
    lineWidth: 2,
  });
  const key = state.pnlMode === "leveraged" ? "cum_leveraged" : "cum_raw";
  series.setData(points.map(p => ({ time: p.ts, value: p[key] })));
  state.curveChart = chart;
  state.curveSeries = series;
  new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
}

function renderBySym() {
  const tbody = $("bySymBody");
  $("bySymCount").textContent = `${state.bySym.length} symbol${state.bySym.length === 1 ? "" : "s"}`;
  if (!state.bySym.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="none">No trades</td></tr>`;
    return;
  }
  const { col, dir } = state.bySymSort;
  const sorted = [...state.bySym].sort((a, b) => {
    const va = a[col], vb = b[col];
    if (typeof va === "string") return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    return dir === "asc" ? va - vb : vb - va;
  });
  tbody.innerHTML = sorted.map(r => {
    const cls = r.pnl_leveraged > 0 ? "pos" : r.pnl_leveraged < 0 ? "neg" : "";
    const pnl = state.pnlMode === "leveraged" ? r.pnl_leveraged : r.pnl_raw;
    const selClass = state.symbolFilter === r.symbol ? "sel" : "";
    return `<tr class="row ${selClass}" data-symbol="${r.symbol}">
      <td>${r.symbol}</td>
      <td class="num">${fmt0(r.trades)}</td>
      <td class="num pos">${fmt0(r.wins)}</td>
      <td class="num neg">${fmt0(r.losses)}</td>
      <td class="num">${r.win_rate.toFixed(1)}%</td>
      <td class="num ${cls}">${fmt2(pnl)}</td>
    </tr>`;
  }).join("");
  // Header sort indicators
  document.querySelectorAll("#bySymTable th.sortable").forEach(th => {
    th.classList.remove("asc", "desc");
    if (th.dataset.sort === col) th.classList.add(dir);
  });
}

function renderTrades(trades) {
  const tbody = $("tradesBody");
  if (!trades.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="none">No trades</td></tr>`;
    return;
  }
  tbody.innerHTML = trades.map(t => {
    const dirClass = t.direction === "LONG" ? "long" : "short";
    const pnl = state.pnlMode === "leveraged" ? t.pnl_leveraged : t.pnl_raw;
    const cls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
    const tagHtml = renderSignalTag(t.signal_tags);
    return `<tr>
      <td>${t.symbol}</td>
      <td><span class="dir ${dirClass}">${t.direction}</span></td>
      <td>${tagHtml}</td>
      <td>${tsToIst(t.entry_ts)}</td>
      <td class="num">${fmt2(t.entry_price)}</td>
      <td>${tsToIst(t.exit_ts)}</td>
      <td class="num">${fmt2(t.exit_price)}</td>
      <td><span class="reason">${t.exit_reason}</span></td>
      <td class="num ${cls}">${fmt2(pnl)}</td>
    </tr>`;
  }).join("");
}

function renderSignalTag(tags) {
  if (!tags) return `<span class="tag none">—</span>`;
  // Render one badge per tag in the comma/plus-separated list (e.g. "basic+divergence").
  const parts = tags.split(/[+,]/).map(p => p.trim()).filter(Boolean);
  return parts.map(p => {
    const cls = p === "divergence" ? "div"
              : p === "basic"      ? "basic"
              : "other";
    return `<span class="tag ${cls}">${p}</span>`;
  }).join(" ");
}

// ============================================================== events
function getCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function bindEvents() {
  $("runPicker").addEventListener("change", e => {
    if (e.target.value) selectRun(Number(e.target.value));
  });

  $("modeLeveraged").addEventListener("click", () => {
    state.pnlMode = "leveraged";
    $("modeLeveraged").classList.add("on");
    $("modeRaw").classList.remove("on");
    refreshCurrent();
  });
  $("modeRaw").addEventListener("click", () => {
    state.pnlMode = "raw";
    $("modeRaw").classList.add("on");
    $("modeLeveraged").classList.remove("on");
    refreshCurrent();
  });

  $("themeBtn").addEventListener("click", () => {
    const root = document.documentElement;
    const cur = root.getAttribute("data-theme") || "dark";
    root.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
  });

  // Sort by-symbol table on header click
  document.querySelectorAll("#bySymTable th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      const dir = (state.bySymSort.col === col && state.bySymSort.dir === "desc") ? "asc" : "desc";
      state.bySymSort = { col, dir };
      renderBySym();
    });
  });

  // Click a by-symbol row to filter the trades table
  $("bySymBody").addEventListener("click", async e => {
    const row = e.target.closest("tr.row");
    if (!row) return;
    const symbol = row.dataset.symbol;
    state.symbolFilter = state.symbolFilter === symbol ? null : symbol;
    $("tradeFilter").textContent = state.symbolFilter ? `filtered: ${state.symbolFilter}` : "";
    const url = state.symbolFilter
      ? `/api/backtests/runs/${state.currentRunId}/trades?symbol=${encodeURIComponent(symbol)}&limit=500`
      : `/api/backtests/runs/${state.currentRunId}/trades?limit=500`;
    const r = await getJSON(url);
    renderTrades(r.trades || []);
    renderBySym();   // re-render to update .sel class
  });
}

async function refreshCurrent() {
  if (state.currentRunId == null) return;
  await selectRun(state.currentRunId);
}

bindEvents();
loadRuns();
