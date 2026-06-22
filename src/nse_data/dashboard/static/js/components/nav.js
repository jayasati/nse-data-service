// Shared top navigation — mounted on every page (one source of truth).
// Renders a consistent nav into header .hdr-right, removes ad-hoc per-page links, and
// highlights the active page. Add a new page here once and it appears everywhere.

const NAV = [
  ["Health", "/"],
  ["Market", "/market"],
  ["Stocks", "/stocks"],
  ["Rankings", "/rankings"],          // weekly positional book
  ["Research", "/research"],           // per-stock catalyst study
  ["Conviction", "/conviction"],       // 13-stage swing synthesis
  ["Intraday", "/intraday"],           // live VWAP/ORB/rvol board
  ["Backtest", "/backtest"],
  ["Trades", "/trades"],
  ["Earnings", "/earnings"],
  ["Gates", "/gates"],
  ["Strategy", "/strategy"],          // backtest study
  ["Signals", "/strategy-signals"],   // live strategy signals
  ["LLM", "/llm"],
];

function mount() {
  let right = document.querySelector("header .hdr-right");
  if (!right) {                                  // future page with a bare header → make one
    const header = document.querySelector("header");
    if (!header) return;
    right = document.createElement("div");
    right.className = "hdr-right";
    header.appendChild(right);
  }
  right.querySelectorAll("a.link").forEach(a => a.remove());   // drop ad-hoc page links
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const nav = document.createElement("nav");
  nav.className = "topnav";
  nav.innerHTML = NAV.map(([label, href]) => {
    const active = href === "/" ? path === "/" : path === href;
    return `<a class="navlink${active ? " on" : ""}" href="${href}">${label}</a>`;
  }).join("");
  right.prepend(nav);
}

if (document.readyState !== "loading") mount();
else document.addEventListener("DOMContentLoaded", mount);
