"""ntfy command listener — turns ntfy from push-only into two-way phone control.

You publish a command (e.g. '/prebuy RELIANCE') to a SECRET command topic from the ntfy app; the
server subscribes to that topic's JSON stream, runs it, and pushes the result back on the signals
channel. Commands:
    /prebuy SYMBOL   → the pre-buy synthesis card (signals + LLM read)
    /help            → list commands

Enable by setting NTFY_TOPIC_CMD in the server .env (unset → the listener doesn't start, safe no-op).
Keep that topic secret — anyone who knows it can run these (read-only) commands. Replies go to a
DIFFERENT topic (signals), so there's no echo loop.
"""
from __future__ import annotations

import json
import os
import threading
import time

import structlog

from .notify import ntfy_send

log = structlog.get_logger(__name__)

HELP = ("📋 Commands:\n/prebuy SYMBOL — full signal card + LLM read\n"
        "/score SYMBOL — quick quantitative scoreboard\n/help — this list")


def _do_prebuy(db_path: str, symbol: str) -> str:
    from ..research.prebuy_card import synthesize
    from ..storage.db import open_db
    conn = open_db(db_path)
    try:
        r = synthesize(conn, symbol)
    finally:
        conn.close()
    if r["n_signals"] == 0:
        return f"{symbol.upper()}: no signals on this name."
    return f"📋 {r['symbol']}\n\n{r['synthesis']}\n\n— {r['n_signals']} signals · ${r['cost_usd']:.4f}"


def _do_score(db_path: str, symbol: str) -> str:
    from ..research.prebuy_card import score_card
    from ..storage.db import open_db
    conn = open_db(db_path)
    try:
        return score_card(conn, symbol)
    finally:
        conn.close()


def handle_command(text: str, db_path: str) -> str | None:
    """Parse + dispatch one command. None → ignore (don't reply to unknown/non-commands)."""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lstrip("/").lower()
    if cmd == "prebuy":
        return _do_prebuy(db_path, parts[1]) if len(parts) >= 2 else "Usage: /prebuy SYMBOL"
    if cmd == "score":
        return _do_score(db_path, parts[1]) if len(parts) >= 2 else "Usage: /score SYMBOL"
    if cmd in ("help", "start"):
        return HELP
    return None


def _process(text: str, db_path: str) -> None:
    try:
        reply = handle_command(text, db_path)
    except Exception as e:  # noqa: BLE001
        log.exception("command_failed")
        reply = f"⚠️ command error: {type(e).__name__}"
    if reply:
        ntfy_send(reply, channel="signals", title="prebuy")


def listen(db_path: str, stop: threading.Event | None = None) -> None:
    import requests
    topic = os.environ.get("NTFY_TOPIC_CMD")
    if not topic:
        log.info("command_listener_disabled")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    token = os.environ.get("NTFY_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{server}/{topic}/json"
    seen: set[str] = set()
    log.info("command_listener_started", topic=topic)
    while not (stop and stop.is_set()):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(10, 75)) as resp:
                for line in resp.iter_lines(decode_unicode=True):
                    if stop and stop.is_set():
                        break
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("event") != "message":
                        continue          # skip 'open' / 'keepalive'
                    mid = msg.get("id", "")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    if len(seen) > 300:
                        seen.clear()
                    body = (msg.get("message") or "").strip()
                    if body:
                        log.info("command_received", cmd=body[:48])
                        _process(body, db_path)
        except Exception as e:  # noqa: BLE001
            log.warning("command_listener_reconnect", err=str(e)[:80])
            time.sleep(5)


def register_command_listener(db_path: str) -> str | None:
    """Start the listener in a daemon thread. No-op (returns None) if NTFY_TOPIC_CMD is unset."""
    if not os.environ.get("NTFY_TOPIC_CMD"):
        return None
    threading.Thread(target=listen, args=(db_path,), name="ntfy-cmd", daemon=True).start()
    return "command_listener"
