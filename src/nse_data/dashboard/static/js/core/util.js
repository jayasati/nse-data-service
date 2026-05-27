// Shared DOM + formatting helpers (no side effects).
export const $ = id => document.getElementById(id);

export const fmt = n => n == null ? "—" : (+n).toLocaleString("en-IN");
export const fmt2 = n => n == null ? "—"
  : (+n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// hex -> rgba string with alpha.
export const rgba = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};

// seconds -> compact "ago" label.
export function ago(sec) {
  if (sec == null) return "—";
  const s = Math.max(0, sec);
  if (s < 90) return Math.round(s) + "s";
  if (s < 5400) return Math.round(s / 60) + "m";
  if (s < 172800) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}
