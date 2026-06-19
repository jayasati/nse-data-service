// Thin client for the stocks API. One method per endpoint.
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();

export const Api = {
  top: (limit = 1000, by = "turnover", source = "auto") =>
    getJSON(`/api/stocks/top?limit=${limit}&by=${by}&source=${source}`),
  // `end` (YYYY-MM-DD, IST) anchors the view to a past date; null = live/latest.
  history: (symbol, interval, days, end = null) =>
    getJSON(`/api/stocks/${encodeURIComponent(symbol)}/history?interval=${interval}&days=${days}`
      + (end ? `&end=${end}` : "")),
  meta: symbol =>
    getJSON(`/api/stocks/${encodeURIComponent(symbol)}/meta`),
  indicators: (symbol, days = 365, cadence = null, end = null) => {
    const q = `days=${days}` + (cadence ? `&cadence=${cadence}` : "") + (end ? `&end=${end}` : "");
    return getJSON(`/api/stocks/${encodeURIComponent(symbol)}/indicators?${q}`);
  },
  search: (q, limit = 20) =>
    getJSON(`/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  // Daily ranking-engine composite + per-factor scores for the chart overlay/badge.
  scoreHistory: (symbol, days = 365) =>
    getJSON(`/api/stocks/${encodeURIComponent(symbol)}/score-history?days=${days}`),
};
