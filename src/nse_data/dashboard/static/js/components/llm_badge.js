// Live LLM-spend badge — mounts itself into every page header (.hdr-right):
// "⚡ LLM $0.14 · 1%" linking to /llm, colored by % of the daily cap (green →
// amber ≥60% → red ≥90%, matching the /llm page thresholds). Zero-dep
// side-effect module: `import "../components/llm_badge.js"` is the whole API.
// Skips mounting on /llm itself and fails silent if the API is down — the
// badge is a convenience, never a page break.

const usd = v => "$" + (+v).toLocaleString("en-US",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function mount() {
  if (location.pathname === "/llm") return;
  const host = document.querySelector(".hdr-right");
  if (!host || document.getElementById("llmBadge")) return;

  const a = document.createElement("a");
  a.id = "llmBadge";
  a.className = "llmbadge";
  a.href = "/llm";
  a.title = "LLM spend today vs daily cap — click for the full usage page";
  a.innerHTML = `⚡ LLM <span class="v">…</span>`;
  host.prepend(a);

  async function refresh() {
    try {
      const r = await (await fetch("/api/llm/spend", { cache: "no-store" })).json();
      const pct = r.cap_usd ? r.today.spend_usd / r.cap_usd * 100 : 0;
      a.querySelector(".v").textContent = `${usd(r.today.spend_usd)} · ${pct.toFixed(0)}%`;
      a.classList.toggle("hot", pct >= 90);
      a.classList.toggle("warn", pct >= 60 && pct < 90);
    } catch (e) {
      a.querySelector(".v").textContent = "—";
    }
  }
  refresh();
  setInterval(refresh, 60_000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
