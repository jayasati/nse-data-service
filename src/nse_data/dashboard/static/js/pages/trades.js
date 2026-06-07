// Live paper-trades page. Loads portfolio overview, per-strategy success rate,
// and the trades table. Status filter (All/Open/Closed) + click a strategy row
// to filter the trades table to that strategy.

const $ = id => document.getElementById(id);

const fmt0 = n => n == null ? "—" : (+n).toLocaleString("en-IN");
const fmt2 = n => n == null ? "—"
  : (+n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pnlCls = n => n == null ? "" : n > 0 ? "pos" : n < 0 ? "neg" : "";
// detected_at / entry_time are ISO-8601 IST strings; show "YYYY-MM-DD HH:MM".
const fmtTime = s => s ? s.slice(0, 16).replace("T", " ") : "—";

const STRATEGY_LABELS = {
  long_buildup: "Long Buildup",
  breakout_52wh: "52w Breakout",
};
const label = s => STRATEGY_LABELS[s] || s;

let state = {
  statusFilter: null,      // null | "open" | "closed"
  strategyFilter: null,
};

const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();

// ============================================================== load
async function load() {
  const [overview, byStrat] = await Promise.all([
    getJSON("/api/trades/overview"),
    getJSON("/api/trades/by-strategy"),
  ]);
  renderMeta(overview);
  renderCards(overview);
  renderByStrategy(byStrat.by_strategy || []);
  await loadTrades();
}

async function loadTrades() {
  const params = new URLSearchParams({ limit: "500" });
  if (state.statusFilter) params.set("status", state.statusFilter);
  if (state.strategyFilter) params.set("strategy", state.strategyFilter);
  const r = await getJSON(`/api/trades?${params}`);
  renderTrades(r.trades || []);
  $("tradeFilter").textContent = [
    state.strategyFilter ? label(state.strategyFilter) : null,
    state.statusFilter,
  ].filter(Boolean).join(" · ");
}

// ============================================================== renderers
function renderMeta(o) {
  if (!o.total) {
    $("meta").textContent = "No paper trades yet — they appear as the live tracker runs during market hours.";
    return;
  }
  $("meta").textContent = `${o.total} trades · ${o.open} open · ${o.closed} closed`;
}

function renderCards(o) {
  $("cTotal").textContent = fmt0(o.total);
  $("cOpen").textContent = fmt0(o.open);
  $("cClosed").textContent = fmt0(o.closed);
  $("cWinRate").textContent = o.closed ? `${o.win_rate.toFixed(1)}%` : "—";
  $("cWL").textContent = `${o.wins} / ${o.losses}`;
  const pnlEl = $("cPnl");
  pnlEl.textContent = `₹ ${fmt2(o.net_pnl)}`;
  pnlEl.className = `card-val ${pnlCls(o.net_pnl)}`;
}

function renderByStrategy(rows) {
  const tbody = $("byStratBody");
  $("byStratCount").textContent = `${rows.length} strateg${rows.length === 1 ? "y" : "ies"}`;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="none">No trades yet</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const sel = state.strategyFilter === r.strategy ? "sel" : "";
    return `<tr class="row ${sel}" data-strategy="${r.strategy}">
      <td>${label(r.strategy)}</td>
      <td class="num">${fmt0(r.total)}</td>
      <td class="num">${fmt0(r.open)}</td>
      <td class="num pos">${fmt0(r.wins)}</td>
      <td class="num neg">${fmt0(r.losses)}</td>
      <td class="num">${r.closed ? r.win_rate.toFixed(1) + "%" : "—"}</td>
      <td class="num ${pnlCls(r.net_pnl)}">${fmt2(r.net_pnl)}</td>
      <td class="num pos">${fmt2(r.avg_win)}</td>
      <td class="num neg">${fmt2(r.avg_loss)}</td>
    </tr>`;
  }).join("");
}

function renderTrades(trades) {
  const tbody = $("tradesBody");
  if (!trades.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="none">No trades</td></tr>`;
    return;
  }
  tbody.innerHTML = trades.map(t => {
    const stClass = t.status === "open" ? "open" : "closed";
    return `<tr>
      <td>${t.symbol}</td>
      <td>${label(t.strategy)}</td>
      <td><span class="status ${stClass}">${t.status}</span></td>
      <td>${fmtTime(t.entry_time)}</td>
      <td class="num">${fmt2(t.entry_price)}</td>
      <td class="num">${fmt2(t.sl_price)}</td>
      <td class="num">${fmt2(t.t1_price)}</td>
      <td>${fmtTime(t.exit_time)}</td>
      <td class="num">${fmt2(t.exit_price)}</td>
      <td>${t.exit_reason ? `<span class="reason">${t.exit_reason}</span>` : "—"}</td>
      <td class="num ${pnlCls(t.net_pnl)}">${t.net_pnl == null ? "—" : fmt2(t.net_pnl)}</td>
    </tr>`;
  }).join("");
}

// ============================================================== events
function bindEvents() {
  const setStatus = (val, btn) => {
    state.statusFilter = val;
    for (const id of ["fAll", "fOpen", "fClosed"]) $(id).classList.toggle("on", id === btn);
    loadTrades();
  };
  $("fAll").addEventListener("click", () => setStatus(null, "fAll"));
  $("fOpen").addEventListener("click", () => setStatus("open", "fOpen"));
  $("fClosed").addEventListener("click", () => setStatus("closed", "fClosed"));

  $("byStratBody").addEventListener("click", e => {
    const row = e.target.closest("tr.row");
    if (!row) return;
    const s = row.dataset.strategy;
    state.strategyFilter = state.strategyFilter === s ? null : s;
    load();   // re-render so the row .sel highlight + trade list both update
  });

  $("themeBtn").addEventListener("click", () => {
    const root = document.documentElement;
    const cur = root.getAttribute("data-theme") || "dark";
    root.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
  });
}

bindEvents();
load();
