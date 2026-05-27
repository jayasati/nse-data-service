// Health dashboard page controller.
import { $, ago } from "../core/util.js";
import { initThemeToggle } from "../core/theme.js";

const COLORS = { ok: "--ok", stale: "--stale", down: "--down", no_table: "--no_table",
                 empty: "--empty", disabled: "--disabled", unknown: "--unknown" };
const LABEL = { ok: "OK", stale: "STALE", down: "DOWN", no_table: "NO TABLE",
                empty: "EMPTY", disabled: "DISABLED", unknown: "UNKNOWN" };

const color = st => getComputedStyle(document.documentElement)
  .getPropertyValue(COLORS[st] || "--unknown");

async function refresh() {
  let r;
  try { r = await (await fetch("/api/health", { cache: "no-store" })).json(); }
  catch (e) { $("meta").textContent = "fetch failed: " + e; return; }

  const gen = new Date(r.generated_at);
  $("meta").textContent =
    `${r.total} collectors · freshest feed ${ago(r.freshest_age_seconds)} ago · generated ${gen.toLocaleTimeString()}`;

  const order = ["down", "no_table", "stale", "empty", "unknown", "ok", "disabled"];
  const sum = $("summary"); sum.innerHTML = "";
  for (const st of order) {
    const n = r.summary[st]; if (!n) continue;
    const p = document.createElement("span"); p.className = "pill";
    p.innerHTML = `<span class="dot" style="background:${color(st)}"></span>${LABEL[st]} <b>${n}</b>`;
    sum.appendChild(p);
  }

  const tb = $("rows"); tb.innerHTML = "";
  for (const c of r.collectors) {
    const tr = document.createElement("tr");
    const tcell = c.table
      ? `<a class="tbl" href="/api/table/${c.table}" target="_blank">${c.table}</a>`
      : '<span class="tbl">—</span>';
    tr.innerHTML =
      `<td><span class="dot" style="background:${color(c.status)}"></span>` +
        `<span class="badge" style="color:${color(c.status)}">${LABEL[c.status]}</span></td>` +
      `<td class="name">${c.name}</td>` +
      `<td>${tcell}</td>` +
      `<td class="tbl">${c.cadence || "—"}</td>` +
      `<td class="num">${c.rows != null ? c.rows.toLocaleString() : "—"}</td>` +
      `<td>${ago(c.data_age_seconds)}${c.data_age_seconds != null ? " ago" : ""}</td>` +
      `<td class="num">${c.lag_seconds != null && c.lag_seconds > 0 ? ago(c.lag_seconds) : "—"}</td>`;
    tb.appendChild(tr);
  }
}

initThemeToggle("themeBtn", refresh);  // re-color on toggle (inline colours)
refresh();
setInterval(refresh, 15000);
