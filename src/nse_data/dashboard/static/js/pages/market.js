import "../components/llm_badge.js";
// Market-context page. Shows the latest regime snapshot (cards) and the sector
// RS leaderboard (table). Auto-refreshes every 30s; both jobs write every 5 min
// during market hours, so off-hours this just shows the last session's values.

const $ = id => document.getElementById(id);

const fmt2 = n => n == null ? "—"
  : (+n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signCls = n => n == null ? "" : n > 0 ? "pos" : n < 0 ? "neg" : "";
const fmtTime = s => s ? s.slice(0, 16).replace("T", " ") : "—";

// regime -> card colour class
const REGIME_CLS = { risk_on: "pos", risk_off: "neg", panic: "neg", neutral: "" };
const TREND_CLS = { improving: "pos", deteriorating: "neg", flat: "" };
const sectorLabel = s => (s || "").replace("NIFTY ", "");

const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();

// ============================================================== load
async function load() {
  const [regimeR, sectorR] = await Promise.all([
    getJSON("/api/market/regime"),
    getJSON("/api/market/sectors"),
  ]);
  renderRegime(regimeR.regime);
  renderSectors(sectorR);
}

// ============================================================== renderers
function renderRegime(r) {
  if (!r) {
    $("meta").textContent = "No market data yet — populates every 5 min during market hours.";
    for (const id of ["cRegime", "cVix", "cNifty", "cBreadth", "cAd", "cConf"]) $(id).textContent = "—";
    return;
  }
  $("meta").textContent = `as of ${fmtTime(r.as_of)} IST`;

  const reg = $("cRegime");
  reg.textContent = (r.overall_regime || "—").replace("_", " ");
  reg.className = `card-val ${REGIME_CLS[r.overall_regime] || ""}`;

  $("cVix").textContent = r.vix_level == null ? "—"
    : `${fmt2(r.vix_level)} · ${r.vix_state || ""} ${arrow(r.vix_direction)}`;

  const nifty = $("cNifty");
  nifty.textContent = r.nifty_return_pct == null ? "—"
    : `${fmt2(r.nifty_return_pct)}% · ${r.nifty_direction || ""}`;
  nifty.className = `card-val ${signCls(r.nifty_return_pct)}`;

  $("cBreadth").textContent = r.pct_above_vwap == null ? "—" : `${fmt2(r.pct_above_vwap)}%`;
  $("cAd").textContent = fmt2(r.advance_decline_ratio);
  $("cConf").textContent = r.regime_confidence == null ? "—" : fmt2(r.regime_confidence);
}

function arrow(dir) {
  if (dir === "rising") return "↑";
  if (dir === "falling") return "↓";
  return "";
}

function renderSectors(s) {
  const tbody = $("sectorBody");
  const rows = s.sectors || [];
  $("sectorAsOf").textContent = s.as_of ? `as of ${fmtTime(s.as_of)}` : "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="none">No sector data yet</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="num">${r.rs_rank ?? "—"}</td>
      <td>${sectorLabel(r.sector_name)}</td>
      <td class="${TREND_CLS[r.rs_trend] || ""}">${r.rs_trend || "—"}</td>
      <td class="num ${signCls(r.sector_return_pct)}">${fmt2(r.sector_return_pct)}</td>
      <td class="num">${fmt2(r.rs_ratio)}</td>
    </tr>`).join("");
}

// ============================================================== events
$("themeBtn").addEventListener("click", () => {
  const root = document.documentElement;
  const cur = root.getAttribute("data-theme") || "dark";
  root.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
});

load();
setInterval(load, 30_000);
