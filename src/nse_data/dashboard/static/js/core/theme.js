// Light/dark theme — persisted in localStorage, shared across both pages.
const KEY = "nse_theme";

export function getTheme() {
  try { return localStorage.getItem(KEY) || "dark"; } catch (e) { return "dark"; }
}

// Wire a toggle button. `onToggle(theme)` fires only on user clicks (not on init),
// so callers can re-theme charts / re-render colours without a double initial pass.
export function initThemeToggle(btnId, onToggle) {
  const btn = document.getElementById(btnId);
  const set = t => {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem(KEY, t); } catch (e) {}
    if (btn) btn.textContent = t === "light" ? "🌙" : "☀";
  };
  if (btn) btn.onclick = () => {
    const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    set(t); if (onToggle) onToggle(t);
  };
  set(getTheme());
}
