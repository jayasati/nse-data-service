// Symbol search box with an autocomplete dropdown.
import { Api } from "../core/api.js";
import { $, fmt2 } from "../core/util.js";

export class SearchBox {
  constructor({ inputId, dropdownId, onSelect, inWatchlist }) {
    this.input = $(inputId);
    this.drop = $(dropdownId);
    this.onSelect = onSelect;
    this.inWatchlist = inWatchlist || (() => false);
    this.timer = null;
    this._wire();
  }

  _show(html) {
    this.drop.innerHTML = html || "";
    this.drop.style.display = html ? "block" : "none";
  }

  async _query(q) {
    try {
      const items = (await Api.search(q)).results || [];
      if (!items.length) { this._show('<div class="ditem none">no match</div>'); return; }
      this._show(items.map(s => {
        const cls = s.pct_change > 0 ? "up" : s.pct_change < 0 ? "down" : "";
        const sign = s.pct_change > 0 ? "+" : "";
        return `<div class="ditem" data-s="${s.symbol}">
          <span class="dsym">${this.inWatchlist(s.symbol) ? "★ " : ""}${s.symbol}</span>
          <span class="dlast">${fmt2(s.last)}</span>
          <span class="dchg ${cls}">${s.pct_change == null ? "" : sign + s.pct_change + "%"}</span></div>`;
      }).join(""));
      for (const it of this.drop.children)
        if (it.dataset.s) it.onmousedown = () => this._choose(it.dataset.s);  // mousedown beats blur
    } catch (e) { this._show(""); }
  }

  _choose(sym) { this.input.value = ""; this.input.blur(); this._show(""); this.onSelect(sym); }

  _wire() {
    this.input.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this._query(this.input.value.trim()), 140);
    });
    this.input.addEventListener("focus", () => this._query(this.input.value.trim()));
    this.input.addEventListener("blur", () => setTimeout(() => this._show(""), 150));
    this.input.addEventListener("keydown", e => {
      if (e.key === "Enter") { const f = this.drop.querySelector(".ditem[data-s]"); if (f) this._choose(f.dataset.s); }
      else if (e.key === "Escape") { this._show(""); this.input.blur(); }
    });
  }
}
