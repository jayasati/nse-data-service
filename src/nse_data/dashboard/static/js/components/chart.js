// ChartController — owns the lightweight-charts price chart, overlays, volume,
// indicator sub-panes, crosshair tooltip, theming and resize. The page tells it
// what to draw; it knows nothing about app state (timeframe/search/etc.).
import { heikin } from "./indicators.js";
import { $, fmt, fmt2, rgba } from "../core/util.js";

const GREEN = "#00b386", RED = "#eb5b3c", VWAPC = "#7c6cdb", BBC = "#94a3b8";
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

    this.chart = LW.createChart(this.chartEl, baseOpts());
    this.candle = this.chart.addCandlestickSeries({ upColor: GREEN, downColor: RED, borderVisible: false, wickUpColor: GREEN, wickDownColor: RED });
    this.area = this.chart.addAreaSeries({ lineWidth: 2, lineType: 2, priceLineVisible: false, lineColor: GREEN, topColor: rgba(GREEN, .28), bottomColor: rgba(GREEN, .02), crosshairMarkerRadius: 4 });
    this.vol = this.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    this.vol.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
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
    // The "score" pane is an independent feature (not a server indicator), so it
    // survives indicator reloads — the score loader owns its lifecycle.
    for (const k of Object.keys(this.srvPanes)) if (k !== "score") this.hideServerOscillator(k);
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
    const label = name.split(":").pop().toUpperCase();
    pane.labelEl.textContent = last ? `${label}  ${last.value.toFixed(2)}` : label;
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
    // `name` is the indicator slug, or "<indicator>:<group>" for a sub-pane
    // group of a mixed-scale overlay block — label/match on the last segment.
    const base = name.split(":").pop();
    const wrap = document.createElement("div"); wrap.className = "pane";
    const lab = document.createElement("div"); lab.className = "plabel"; lab.textContent = base.toUpperCase();
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
        // DI columns keep the conventional colors (+DI green, −DI red) so an
        // ADX pane reads the same as every charting package.
        const c = col === "di_plus" ? GREEN : col === "di_minus" ? RED
          : i === 0 ? color : SRV_COLORS[(SRV_COLORS.indexOf(color) + i) % SRV_COLORS.length];
        if (!col.endsWith("_hist")) primaryCol = primaryCol.endsWith("_hist") ? col : primaryCol;
        series[col] = ch.addLineSeries({ color: c, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
      }
    });
    // RSI gets the canonical 70/30/50 reference lines so the label panel
    // gives an instant sense of "is this overbought/oversold". Matches the
    // EOD pane ("rsi"), the intraday one ("rsi_5m") and group panes.
    if (base === "rsi" || base.startsWith("rsi_")) {
      const ref = series[primaryCol];
      [[70, RED], [30, GREEN], [50, BBC]].forEach(([p, c]) =>
        ref.createPriceLine({ price: p, color: c, lineStyle: 2, lineWidth: 1, axisLabelVisible: true }));
    }
    // ADX gets the 25 trend-strength threshold for the same reason.
    if (base === "adx" || base.startsWith("adx_")) {
      series[primaryCol].createPriceLine({ price: 25, color: BBC, lineStyle: 2, lineWidth: 1, axisLabelVisible: true });
    }
    // Ranking-engine score pane: 50 = sector-median reference (above = top half).
    if (base === "score") {
      series[primaryCol].createPriceLine({ price: 50, color: BBC, lineStyle: 2, lineWidth: 1, axisLabelVisible: true });
    }
    // CHOP's conventional choppy/trending thresholds.
    if (base === "chop" || base.startsWith("chop_")) {
      const ref = series[primaryCol];
      [61.8, 38.2].forEach(p =>
        ref.createPriceLine({ price: p, color: BBC, lineStyle: 2, lineWidth: 1, axisLabelVisible: true }));
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
    // Server panes resize themselves via their own ResizeObservers.
  }

  // ---- theming ----
  _themeOne(ch) {
    const css = getComputedStyle(document.documentElement), g = v => css.getPropertyValue(v).trim();
    ch.applyOptions({ layout: { background: { type: "solid", color: g("--panel") }, textColor: g("--dim") },
                      grid: { vertLines: { visible: false }, horzLines: { color: g("--grid") } } });
  }
  themeAll() { this.allCharts.forEach(c => this._themeOne(c)); }

  // ---- crosshair tooltip ----
  _wireTooltip() {
    this.chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        this.tip.style.display = "none";
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
}
