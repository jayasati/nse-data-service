import "../components/llm_badge.js";
// Earnings-reaction page. Shows the engine's accuracy/probability (direction-
// adjusted T+1 win rate) + long/short split, recent reactions, and the pre-event
// "what's priced in" setups. Side filter (All/Long/Short) re-filters reactions.

const $ = id => document.getElementById(id);
const fmt2 = n => n == null ? "—"
  : (+n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = n => n == null ? "—" : `${(+n).toFixed(1)}%`;
const signed = n => n == null ? "—" : `${n > 0 ? "+" : ""}${(+n).toFixed(2)}%`;
const cls = n => n == null ? "" : n > 0 ? "pos" : n < 0 ? "neg" : "";
const fmtTime = s => s ? s.slice(0, 16).replace("T", " ") : "—";

let state = { side: null };   // null | "long" | "short"
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();

async function load() {
  const odds = await getJSON("/api/earnings/odds");
  renderCards(odds);
  await Promise.all([loadReactions(), loadUpcoming()]);
}

function renderCards(o) {
  const ov = o.overall || {};
  const cov = o.coverage || {};
  const enough = (ov.n || 0) >= (o.min_samples || 20);
  $("meta").textContent = enough
    ? `${cov.settled}/${cov.total} settled · ${cov.longs} long / ${cov.shorts} short`
    : `Building history — ${cov.settled || 0} settled (need ${o.min_samples} for stable odds). Run the backfill to bootstrap.`;
  $("cWin").textContent = ov.win_rate == null ? "—" : pct(ov.win_rate);
  $("cAvg").textContent = signed(ov.avg_return_pct);
  $("cAvg").className = `card-val ${cls(ov.avg_return_pct)}`;
  $("cPF").textContent = ov.profit_factor == null ? "—" : fmt2(ov.profit_factor);
  $("cN").textContent = `${cov.settled ?? "—"} / ${cov.total ?? "—"}`;
  $("cLong").textContent = (o.long || {}).win_rate == null ? "—" : `${pct(o.long.win_rate)} (n=${o.long.n})`;
  $("cShort").textContent = (o.short || {}).win_rate == null ? "—" : `${pct(o.short.win_rate)} (n=${o.short.n})`;
}

async function loadReactions() {
  const params = new URLSearchParams({ limit: "300" });
  if (state.side) params.set("direction", state.side);
  const r = await getJSON(`/api/earnings/reactions?${params}`);
  $("rxFilter").textContent = state.side || "";
  renderReactions(r.reactions || []);
}

function renderReactions(rows) {
  const tb = $("rxBody");
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="8" class="none">No reactions yet</td></tr>`; return; }
  tb.innerHTML = rows.map(x => {
    const result = x.win == null ? "—"
      : `<span class="status ${x.win ? "closed" : "open"}">${x.win ? "win" : "loss"}</span>`;
    return `<tr>
      <td><a class="symlink" href="/stocks?symbol=${x.symbol}&tab=results">${x.symbol}</a></td>
      <td>${fmtTime(x.detected_at)}</td>
      <td><span class="reason">${x.direction}</span></td>
      <td class="num ${cls(x.reaction_move_pct)}">${signed(x.reaction_move_pct)}</td>
      <td class="num">${x.confidence == null ? "—" : fmt2(x.confidence)}</td>
      <td class="num ${cls(x.ret_1d)}">${signed(x.ret_1d)}</td>
      <td class="num ${cls(x.outcome_pct)}">${signed(x.outcome_pct)}</td>
      <td>${result}</td>
    </tr>`;
  }).join("");
}

async function loadUpcoming() {
  const r = await getJSON("/api/earnings/upcoming?limit=200");
  const rows = r.upcoming || [];
  $("upCount").textContent = `${rows.length}`;
  const tb = $("upBody");
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="7" class="none">No setups yet</td></tr>`; return; }
  tb.innerHTML = rows.map(s => `<tr>
      <td><a class="symlink" href="/stocks?symbol=${s.symbol}&tab=results">${s.symbol}</a></td>
      <td>${s.event_date}</td>
      <td class="num ${cls(s.run_up_5d)}">${signed(s.run_up_5d)} <span class="dim">${s.run_up_class || ""}</span></td>
      <td class="num">${s.implied_move_pct == null ? "—" : "±" + (+s.implied_move_pct).toFixed(1) + "%"}</td>
      <td class="num">${s.pcr == null ? "—" : fmt2(s.pcr)}</td>
      <td class="num ${cls(s.expectation_proxy_score)}">${s.expectation_proxy_score == null ? "—" : (+s.expectation_proxy_score).toFixed(2)}</td>
      <td>${s.fundamental_class || "—"}</td>
    </tr>`).join("");
}

function bindEvents() {
  const setSide = (val, btn) => {
    state.side = val;
    for (const id of ["fAll", "fLong", "fShort"]) $(id).classList.toggle("on", id === btn);
    loadReactions();
  };
  $("fAll").addEventListener("click", () => setSide(null, "fAll"));
  $("fLong").addEventListener("click", () => setSide("long", "fLong"));
  $("fShort").addEventListener("click", () => setSide("short", "fShort"));
  $("themeBtn").addEventListener("click", () => {
    const root = document.documentElement;
    root.setAttribute("data-theme", (root.getAttribute("data-theme") || "dark") === "dark" ? "light" : "dark");
  });
}

bindEvents();
load();
