// Strategy Signals — live system-generated signals (forward-test) from /api/strategy-analytics/signals.
const $ = id => document.getElementById(id);
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();
const f2 = n => n == null ? "—" : (+n).toFixed(1);
const hm = ts => ts == null ? "—" : String(ts).slice(11, 16);
const epoch = e => {
  if (!e) return "—";
  const d = new Date((e + 19800) * 1000), p = n => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

async function init() {
  const r = await getJSON("/api/strategy-analytics/signals?limit=300");
  const sigs = r.signals || [];
  $("meta").textContent = sigs.length ? `${sigs.length} signals` : "no signals yet — fires live during market hours";
  $("sigBody").innerHTML = sigs.map(s =>
    `<tr><td>${epoch(s.alerted_at)}</td><td>${s.strategy}</td><td>${s.symbol}</td>` +
    `<td>${s.direction === "long" ? "🟢 long" : "🔴 short"}</td><td>${s.daily_trend}</td>` +
    `<td>${hm(s.sweep_time)}</td><td>${hm(s.bos_time)}</td>` +
    `<td>${f2(s.fvg_low)}–${f2(s.fvg_high)}</td><td class="num">${f2(s.entry_price)}</td>` +
    `<td class="num">${f2(s.stop)}</td><td class="num">${f2(s.target)}</td>` +
    `<td class="num">1:${(+s.rr).toFixed(0)}</td><td class="num">${s.qty}</td></tr>`).join("");
}
init();
