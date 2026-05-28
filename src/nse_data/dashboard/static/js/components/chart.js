// ChartController — owns the lightweight-charts price chart, overlays, volume,
// indicator sub-panes, crosshair tooltip, theming and resize. The page tells it
// what to draw; it knows nothing about app state (timeframe/search/etc.).
import { ema, sma, stdev, rsi, macd, adx, chop, vwap, heikin, pair } from "./indicators.js";
import { $, fmt, fmt2, rgba } from "../core/util.js";

const GREEN = "#00b386", RED = "#eb5b3c", VWAPC = "#7c6cdb",
      EMA20C = "#f59e0b", EMA50C = "#3b82f6", EMA200C = "#ec4899", BBC = "#94a3b8";
const LW = window.LightweightCharts;

// Distinct colors for server-computed indicator overlays. Cycles if we ever
// register more series than this list — fine for visual verification.
const SRV_COLORS = ["#22d3ee", "#a78bfa", "#fb7185", "#facc15", "#34d399", "#f97316", "#60a5fa"];

// Force every pane's right-axis to reserve the same horizontal space, so the
// time scale lines up vertically across price + indicator sub-panes. Without
// this lightweight-charts sizes each pane's axis to its own labels — wide
// price values (249.62) vs short RSI values (60) push the plot areas to
// different widths and indicator bars drift sideways from price bars.
const AXIS_MIN_WIDTH = 64;

const baseOpts = () => ({
  layout: { background: { type: "solid", color: "#0e1014" }, textColor: "#7e8696", fontFamily: "Inter, sans-serif" },
  grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,.035)" } },
  rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.12 }, minimumWidth: AXIS_MIN_WIDTH },
  timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
  crosshair: { mode: 1, vertLine: { color: "rgba(130,140,160,.5)", width: 1, style: 3, labelBackgroundColor: GREEN },
               horzLine: { color: "rgba(130,140,160,.5)", width: 1, style: 3, labelBackgroundColor: GREEN } },
});

function fmtTime(t) {
  if (typeof t === "number") {
    const d = new Date(t * 1000), p = n => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  }
  if (t && typeof t === "object") return `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`;
  return t;
}

export class ChartController {
  constructor(chartElId, panesElId, tipElId) {
    this.chartEl = $(chartElId);
    this.panesEl = $(panesElId);
    this.tip = $(tipElId);
    this.allCharts = [];
    this.syncing = false;
    this.panes = {};

    this.chart = LW.createChart(this.chartEl, baseOpts());
    this.candle = this.chart.addCandlestickSeries({ upColor: GREEN, downColor: RED, borderVisible: false, wickUpColor: GREEN, wickDownColor: RED });
    this.area = this.chart.addAreaSeries({ lineWidth: 2, lineType: 2, priceLineVisible: false, lineColor: GREEN, topColor: rgba(GREEN, .28), bottomColor: rgba(GREEN, .02), crosshairMarkerRadius: 4 });
    this.vol = this.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    this.vol.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    this.ov = {
      vwap: this.chart.addLineSeries({ color: VWAPC, lineWidth: 1.5, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }),
      ema20: this.chart.addLineSeries({ color: EMA20C, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false }),
      ema50: this.chart.addLineSeries({ color: EMA50C, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false }),
      ema200: this.chart.addLineSeries({ color: EMA200C, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false }),
      bbu: this.chart.addLineSeries({ color: BBC, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }),
      bbm: this.chart.addLineSeries({ color: BBC, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      bbl: this.chart.addLineSeries({ color: BBC, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }),
    };
    // Server-computed indicator overlays. Keyed by `${indicator}.${column}`
    // (e.g. "sma.sma_20"). Created lazily on first toggle so we don't allocate
    // line series for indicators the user never enables.
    this.srv = {};

    // Server-computed oscillator sub-panes. Keyed by indicator name ("rsi",
    // "macd"). Each entry owns a dedicated chart pane below the main one and
    // a series per output column. Created lazily on first toggle.
    this.srvPanes = {};

    this.allCharts.push(this.chart);
    this._registerSync(this.chart);
    new ResizeObserver(() => this.fit()).observe(this.chartEl);
    requestAnimationFrame(() => this.fit());
    this._wireTooltip();
  }

  // ---- server-computed indicator overlays ----
  // Show one (indicator, column) line drawn from server-side `indicator_*`
  // tables. Daily-only — `points` carry `date` strings, so they align with
  // 1d/1w chart bars but not intraday epoch times. The caller is responsible
  // for hiding these when on an intraday timeframe.
  showServerSeries(key, points, color) {
    let s = this.srv[key];
    if (!s) {
      s = this.chart.addLineSeries({
        color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false,
      });
      this.srv[key] = s;
    }
    s.applyOptions({ color });
    s.setData(points);
  }

  hideServerSeries(key) {
    const s = this.srv[key]; if (!s) return;
    s.setData([]);
  }

  clearServerSeries() {
    for (const k in this.srv) this.srv[k].setData([]);
    for (const k of Object.keys(this.srvPanes)) this.hideServerOscillator(k);
  }

  // ---- server-computed oscillator sub-panes (RSI, MACD, ...) ----
  // Each indicator family with pane="oscillator" gets one stacked sub-pane
  // below the main chart. All output columns of that indicator render inside
  // the pane on a shared 0-bounded y-scale. Columns ending in "_hist" render
  // as a colored histogram (red/green by sign), everything else as a line.
  showServerOscillator(name, columns, pointsByCol, color) {
    let pane = this.srvPanes[name];
    if (!pane) pane = this._makeSrvPane(name, columns, color);
    for (const col of columns) {
      const series = pane.series[col];
      const points = pointsByCol[col] || [];
      if (col.endsWith("_hist")) {
        // Histogram: each point also carries a sign-based color.
        series.setData(points.map(p => ({
          time: p.time, value: p.value,
          color: p.value >= 0 ? rgba(GREEN, .7) : rgba(RED, .7),
        })));
      } else {
        series.setData(points);
      }
    }
    // Show the latest non-null value in the label for at-a-glance verification.
    const primary = pane.primaryCol;
    const last = (pointsByCol[primary] || []).slice(-1)[0];
    pane.labelEl.textContent = last
      ? `${name.toUpperCase()}  ${last.value.toFixed(2)}`
      : name.toUpperCase();
    this.fit();
  }

  hideServerOscillator(name) {
    const pane = this.srvPanes[name]; if (!pane) return;
    const i = this.allCharts.indexOf(pane.ch); if (i >= 0) this.allCharts.splice(i, 1);
    pane.ch.remove(); pane.wrap.remove();
    delete this.srvPanes[name];
    this.fit();
  }

  _makeSrvPane(name, columns, color) {
    const wrap = document.createElement("div"); wrap.className = "pane";
    const lab = document.createElement("div"); lab.className = "plabel"; lab.textContent = name.toUpperCase();
    const el = document.createElement("div"); el.className = "pchart";
    wrap.appendChild(lab); wrap.appendChild(el); this.panesEl.appendChild(wrap);
    const ch = LW.createChart(el, Object.assign(baseOpts(), {
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.2, bottom: 0.1 }, minimumWidth: AXIS_MIN_WIDTH },
    }));
    const series = {};
    let primaryCol = columns[0];
    columns.forEach((col, i) => {
      if (col.endsWith("_hist")) {
        series[col] = ch.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      } else {
        // First non-hist column is "primary" — used for crosshair tooltip + label readout.
        const c = i === 0 ? color : SRV_COLORS[(SRV_COLORS.indexOf(color) + i) % SRV_COLORS.length];
        if (!col.endsWith("_hist")) primaryCol = primaryCol.endsWith("_hist") ? col : primaryCol;
        series[col] = ch.addLineSeries({ color: c, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
      }
    });
    // RSI gets the canonical 70/30/50 reference lines so the label panel
    // gives an instant sense of "is this overbought/oversold".
    if (name === "rsi") {
      const ref = series[primaryCol];
      [[70, RED], [30, GREEN], [50, BBC]].forEach(([p, c]) =>
        ref.createPriceLine({ price: p, color: c, lineStyle: 2, lineWidth: 1, axisLabelVisible: true }));
    }
    new ResizeObserver(() => ch.resize(el.clientWidth, el.clientHeight)).observe(el);
    const pane = { wrap, el, ch, series, labelEl: lab, primaryCol };
    this.srvPanes[name] = pane;
    this.allCharts.push(ch); this._registerSync(ch); this._themeOne(ch);
    return pane;
  }

  // Stable color per server-series key, so toggling on/off keeps the same hue.
  srvColor(index) { return SRV_COLORS[index % SRV_COLORS.length]; }

  _registerSync(ch) {
    // Sync by *time*, not by logical (bar-index) range. Indicator panes used
    // to filter null warm-up bars, so their bar counts diverged from the main
    // chart — propagating a logical range like [50, 200] then meant a
    // different time slice in each pane, and labels like "27" / "28" landed
    // at different x-positions. Time-based sync keeps every pane locked to
    // the same wall-clock window even if their data lengths still differ.
    ch.timeScale().subscribeVisibleTimeRangeChange(r => {
      if (this.syncing || !r) return;
      this.syncing = true;
      this.allCharts.forEach(c => { if (c !== ch) try { c.timeScale().setVisibleRange(r); } catch (e) {} });
      this.syncing = false;
    });
  }

  fit() {
    this.chart.resize(this.chartEl.clientWidth, this.chartEl.clientHeight);
    for (const k in this.panes) { const p = this.panes[k]; p.ch.resize(p.el.clientWidth, p.el.clientHeight); }
  }

  // ---- theming ----
  _themeOne(ch) {
    const css = getComputedStyle(document.documentElement), g = v => css.getPropertyValue(v).trim();
    ch.applyOptions({ layout: { background: { type: "solid", color: g("--panel") }, textColor: g("--dim") },
                      grid: { vertLines: { visible: false }, horzLines: { color: g("--grid") } } });
  }
  themeAll() { this.allCharts.forEach(c => this._themeOne(c)); }

  // ---- indicator sub-panes ----
  addStudy(name, label) { if (!this.panes[name]) this._makePane(name, label); }
  removeStudy(name) { this._dropPane(name); }

  _makePane(name, label) {
    const wrap = document.createElement("div"); wrap.className = "pane";
    const lab = document.createElement("div"); lab.className = "plabel"; lab.textContent = label;
    const el = document.createElement("div"); el.className = "pchart";
    wrap.appendChild(lab); wrap.appendChild(el); this.panesEl.appendChild(wrap);
    const ch = LW.createChart(el, Object.assign(baseOpts(), { rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.2, bottom: 0.1 }, minimumWidth: AXIS_MIN_WIDTH } }));
    const series = {};
    if (name === "rsi") {
      series.line = ch.addLineSeries({ color: VWAPC, lineWidth: 1.5, priceLineVisible: false });
      [[70, RED], [30, GREEN], [50, BBC]].forEach(([p, c]) => series.line.createPriceLine({ price: p, color: c, lineStyle: 2, lineWidth: 1, axisLabelVisible: true }));
    }
    if (name === "macd") {
      series.hist = ch.addHistogramSeries({ priceLineVisible: false });
      series.macd = ch.addLineSeries({ color: EMA50C, lineWidth: 1.5, priceLineVisible: false });
      series.sig = ch.addLineSeries({ color: EMA20C, lineWidth: 1.5, priceLineVisible: false });
    }
    if (name === "adx") {
      series.adx = ch.addLineSeries({ color: VWAPC, lineWidth: 1.5, priceLineVisible: false });
      series.p = ch.addLineSeries({ color: GREEN, lineWidth: 1.2, priceLineVisible: false });
      series.m = ch.addLineSeries({ color: RED, lineWidth: 1.2, priceLineVisible: false });
      series.adx.createPriceLine({ price: 25, color: BBC, lineStyle: 2, lineWidth: 1, axisLabelVisible: true });
    }
    if (name === "chop") {
      series.line = ch.addLineSeries({ color: "#22d3ee", lineWidth: 1.5, priceLineVisible: false });
      [61.8, 38.2].forEach(p => series.line.createPriceLine({ price: p, color: BBC, lineStyle: 2, lineWidth: 1, axisLabelVisible: true }));
    }
    new ResizeObserver(() => ch.resize(el.clientWidth, el.clientHeight)).observe(el);
    this.panes[name] = { wrap, el, ch, series, labelEl: lab, valAt: new Map(), primary: series.line || series.adx || series.macd };
    this.allCharts.push(ch); this._registerSync(ch); this._themeOne(ch);
  }

  _dropPane(name) {
    const p = this.panes[name]; if (!p) return;
    const i = this.allCharts.indexOf(p.ch); if (i >= 0) this.allCharts.splice(i, 1);
    p.ch.remove(); p.wrap.remove(); delete this.panes[name];
  }

  // ---- crosshair tooltip ----
  _wireTooltip() {
    this.chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        this.tip.style.display = "none";
        for (const k in this.panes) try { this.panes[k].ch.clearCrosshairPosition(); } catch (e) {}
        return;
      }
      const cd = param.seriesData.get(this.candle), ad = param.seriesData.get(this.area), vd = param.seriesData.get(this.vol);
      const t = `<b>${fmtTime(param.time)}</b>`;
      const v = vd ? `  <span class="lbl">Vol</span> ${fmt(vd.value)}` : "";
      if (cd) {
        const cls = cd.close >= cd.open ? "up" : "down";
        this.tip.innerHTML = `${t}  <span class="lbl">O</span> ${fmt2(cd.open)} <span class="lbl">H</span> ${fmt2(cd.high)} <span class="lbl">L</span> ${fmt2(cd.low)} <span class="${cls}">C ${fmt2(cd.close)}</span>${v}`;
        this.tip.style.display = "block";
      } else if (ad) {
        this.tip.innerHTML = `${t}  <b>${fmt2(ad.value)}</b>${v}`;
        this.tip.style.display = "block";
      } else this.tip.style.display = "none";
      for (const k in this.panes) {
        const p = this.panes[k], val = p.valAt.get(param.time);
        try { if (val != null) p.ch.setCrosshairPosition(val, param.time, p.primary); else p.ch.clearCrosshairPosition(); } catch (e) {}
      }
    });
  }

  // ---- rendering ----
  // Draw price + volume for `bars` in `mode` (area|candle|ha). Returns summary.
  render(bars, mode) {
    if (!bars.length) { this.candle.setData([]); this.area.setData([]); this.vol.setData([]); return { count: 0 }; }
    const drawBars = mode === "ha" ? heikin(bars) : bars;
    const first = bars[0].close, last = bars[bars.length - 1].close, up = last >= first, col = up ? GREEN : RED;
    if (mode === "area") {
      this.candle.setData([]);
      this.area.applyOptions({ lineColor: col, topColor: rgba(col, .28), bottomColor: rgba(col, .02) });
      this.area.setData(bars.map(b => ({ time: b.time, value: b.close })));
    } else {
      this.area.setData([]);
      this.candle.setData(drawBars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
    }
    this.vol.setData(drawBars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? rgba(GREEN, .45) : rgba(RED, .45) })));
    this.chart.timeScale().fitContent();
    return { up, chg: first ? ((last - first) / first * 100) : 0, count: bars.length };
  }

  // Draw enabled overlays + whatever study panes currently exist.
  drawIndicators(bars, tf, overlays) {
    const times = bars.map(b => b.time), closes = bars.map(b => b.close);
    this.ov.vwap.setData(overlays.vwap && tf === "1D" ? pair(times, vwap(bars)) : []);
    this.ov.ema20.setData(overlays.ema20 ? pair(times, ema(closes, 20)) : []);
    this.ov.ema50.setData(overlays.ema50 ? pair(times, ema(closes, 50)) : []);
    this.ov.ema200.setData(overlays.ema200 ? pair(times, ema(closes, 200)) : []);
    if (overlays.bb) {
      const m = sma(closes, 20), sd = stdev(closes, 20);
      this.ov.bbu.setData(pair(times, closes.map((_, i) => m[i] != null ? m[i] + 2 * sd[i] : null)));
      this.ov.bbm.setData(pair(times, m));
      this.ov.bbl.setData(pair(times, closes.map((_, i) => m[i] != null ? m[i] - 2 * sd[i] : null)));
    } else { this.ov.bbu.setData([]); this.ov.bbm.setData([]); this.ov.bbl.setData([]); }

    const P = this.panes;
    if (P.rsi) {
      const r = rsi(closes, 14), p = pair(times, r);
      P.rsi.series.line.setData(p); P.rsi.valAt = new Map(p.map(x => [x.time, x.value]));
      const lastv = [...r].reverse().find(x => x != null);
      P.rsi.series.line.applyOptions({ color: lastv == null ? VWAPC : lastv > 70 ? RED : lastv < 30 ? GREEN : VWAPC });
      P.rsi.labelEl.textContent = "RSI 14" + (lastv != null ? "  " + lastv.toFixed(1) : "");
    }
    if (P.macd) {
      const { m, sig, hist } = macd(closes);
      P.macd.series.macd.setData(pair(times, m)); P.macd.series.sig.setData(pair(times, sig));
      // Whitespace `{time:t}` for warm-up bars keeps the histogram length equal
      // to the main chart's bar count, so logical-range sync stays aligned.
      P.macd.series.hist.setData(times.map((t, i) => hist[i] == null
        ? { time: t }
        : { time: t, value: hist[i], color: hist[i] >= 0 ? rgba(GREEN, .7) : rgba(RED, .7) }));
      const pm = pair(times, m); P.macd.valAt = new Map(pm.map(x => [x.time, x.value]));
      const lm = [...m].reverse().find(x => x != null), ls = [...sig].reverse().find(x => x != null);
      P.macd.labelEl.textContent = `MACD${lm != null ? " " + lm.toFixed(2) : ""}${ls != null ? "  Sig " + ls.toFixed(2) : ""}`;
    }
    if (P.adx) {
      const { adx: a, pDI, mDI } = adx(bars, 14);
      P.adx.series.adx.setData(pair(times, a)); P.adx.series.p.setData(pair(times, pDI)); P.adx.series.m.setData(pair(times, mDI));
      const pa = pair(times, a); P.adx.valAt = new Map(pa.map(x => [x.time, x.value]));
      const la = [...a].reverse().find(x => x != null);
      P.adx.labelEl.textContent = "ADX" + (la != null ? " " + la.toFixed(1) : "");
    }
    if (P.chop) {
      const c = chop(bars, 14), p = pair(times, c);
      P.chop.series.line.setData(p); P.chop.valAt = new Map(p.map(x => [x.time, x.value]));
      const lc = [...c].reverse().find(x => x != null);
      P.chop.labelEl.textContent = "CHOP" + (lc != null ? " " + lc.toFixed(1) + (lc < 38.2 ? "  TRENDING" : lc > 61.8 ? "  CHOPPY" : "") : "");
    }
  }
}
