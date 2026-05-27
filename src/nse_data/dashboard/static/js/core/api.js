// Thin client for the stocks API. One method per endpoint.
const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();

export const Api = {
  top: (limit = 1000, by = "turnover", source = "auto") =>
    getJSON(`/api/stocks/top?limit=${limit}&by=${by}&source=${source}`),
  history: (symbol, interval, days) =>
    getJSON(`/api/stocks/${encodeURIComponent(symbol)}/history?interval=${interval}&days=${days}`),
  meta: symbol =>
    getJSON(`/api/stocks/${encodeURIComponent(symbol)}/meta`),
  search: (q, limit = 20) =>
    getJSON(`/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`),
};
