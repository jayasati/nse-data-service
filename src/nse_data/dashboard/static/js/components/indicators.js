// Technical-indicator math. Pure functions over arrays / candle dicts.

export function ema(v, p) {
  const k = 2 / (p + 1); let e = null;
  return v.map(x => { if (x == null) return null; e = (e == null) ? x : x * k + e * (1 - k); return e; });
}

export function sma(v, p) {
  const o = []; let s = 0;
  for (let i = 0; i < v.length; i++) { s += v[i]; if (i >= p) s -= v[i - p]; o.push(i >= p - 1 ? s / p : null); }
  return o;
}

export function stdev(v, p) {
  const o = [];
  for (let i = 0; i < v.length; i++) {
    if (i < p - 1) { o.push(null); continue; }
    let m = 0; for (let j = i - p + 1; j <= i; j++) m += v[j]; m /= p;
    let s = 0; for (let j = i - p + 1; j <= i; j++) s += (v[j] - m) ** 2;
    o.push(Math.sqrt(s / p));
  }
  return o;
}

export function rsi(c, p) {
  const o = Array(c.length).fill(null); if (c.length <= p) return o;
  let g = 0, l = 0;
  for (let i = 1; i <= p; i++) { const d = c[i] - c[i - 1]; if (d >= 0) g += d; else l -= d; }
  g /= p; l /= p;
  o[p] = 100 - 100 / (1 + (l === 0 ? 100 : g / l));
  for (let i = p + 1; i < c.length; i++) {
    const d = c[i] - c[i - 1], gg = d > 0 ? d : 0, ll = d < 0 ? -d : 0;
    g = (g * (p - 1) + gg) / p; l = (l * (p - 1) + ll) / p;
    o[i] = 100 - 100 / (1 + (l === 0 ? 100 : g / l));
  }
  return o;
}

export function macd(c) {
  const e12 = ema(c, 12), e26 = ema(c, 26);
  const m = c.map((_, i) => e12[i] - e26[i]);
  const sig = ema(m, 9);
  return { m, sig, hist: m.map((v, i) => v - sig[i]) };
}

export function wilder(v, p) {
  const o = Array(v.length).fill(null); if (v.length < p) return o;
  let s = 0; for (let i = 0; i < p; i++) s += v[i]; o[p - 1] = s / p;
  for (let i = p; i < v.length; i++) o[i] = (o[i - 1] * (p - 1) + v[i]) / p;
  return o;
}

export function trueRange(b) {
  return b.map((x, i) => i === 0 ? x.high - x.low
    : Math.max(x.high - x.low, Math.abs(x.high - b[i - 1].close), Math.abs(x.low - b[i - 1].close)));
}

export function adx(b, p) {
  const pdm = [], mdm = [], tr = trueRange(b);
  for (let i = 0; i < b.length; i++) {
    if (i === 0) { pdm.push(0); mdm.push(0); continue; }
    const up = b[i].high - b[i - 1].high, dn = b[i - 1].low - b[i].low;
    pdm.push(up > dn && up > 0 ? up : 0); mdm.push(dn > up && dn > 0 ? dn : 0);
  }
  const atr = wilder(tr, p), pd = wilder(pdm, p), md = wilder(mdm, p);
  const pDI = [], mDI = [], dx = [];
  for (let i = 0; i < b.length; i++) {
    if (atr[i] == null || atr[i] === 0) { pDI.push(null); mDI.push(null); dx.push(null); continue; }
    const a = 100 * pd[i] / atr[i], c = 100 * md[i] / atr[i]; pDI.push(a); mDI.push(c);
    dx.push((a + c) === 0 ? 0 : 100 * Math.abs(a - c) / (a + c));
  }
  return { adx: wilder(dx.map(x => x == null ? 0 : x), p), pDI, mDI };
}

export function chop(b, p) {
  const o = Array(b.length).fill(null), tr = trueRange(b);
  for (let i = p - 1; i < b.length; i++) {
    let s = 0, hh = -1e18, ll = 1e18;
    for (let j = i - p + 1; j <= i; j++) { s += tr[j]; hh = Math.max(hh, b[j].high); ll = Math.min(ll, b[j].low); }
    const r = hh - ll; o[i] = r > 0 ? 100 * Math.log10(s / r) / Math.log10(p) : null;
  }
  return o;
}

export function vwap(b) {
  let pv = 0, vv = 0;
  return b.map(x => { const tp = (x.high + x.low + x.close) / 3; pv += tp * (x.volume || 0); vv += (x.volume || 0); return vv > 0 ? pv / vv : null; });
}

export function heikin(b) {
  const o = []; let po, pc;
  for (let i = 0; i < b.length; i++) {
    const x = b[i];
    const c = (x.open + x.high + x.low + x.close) / 4;
    const op = i === 0 ? (x.open + x.close) / 2 : (po + pc) / 2;
    o.push({ time: x.time, open: op, high: Math.max(x.high, op, c), low: Math.min(x.low, op, c), close: c, volume: x.volume });
    po = op; pc = c;
  }
  return o;
}

// align a values array to times, dropping null/non-finite points.
export const pair = (times, vals) => {
  const o = [];
  for (let i = 0; i < times.length; i++)
    if (vals[i] != null && isFinite(vals[i])) o.push({ time: times[i], value: vals[i] });
  return o;
};
