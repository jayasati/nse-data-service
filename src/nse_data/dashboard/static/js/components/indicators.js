// Chart-rendering math only. All indicator values (RSI/MACD/ADX/VWAP/EMA/
// Bollinger/Supertrend/ATR/CHOP/…) come from the server's indicator_* tables
// via /api/stocks/{symbol}/indicators — the same rows the prediction bot
// reads — and are NOT recomputed client-side. The browser-side library that
// used to live here produced conflicting values (it ran on whatever bars the
// chart happened to load: a 14-bar RSI over 1-min bars is a 14-minute RSI).
// Heikin-Ashi stays: it's a candle *display* transform, not an indicator.

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
