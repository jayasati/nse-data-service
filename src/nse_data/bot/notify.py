"""ntfy.sh delivery — a Telegram-independent alert channel (for India, where Telegram is
blocked on the receiving side even though the EC2 server can still reach the Telegram API).

Every alert that goes through `dispatcher.send_telegram` is mirrored here, so morning brief /
EOD summary / weekly paper digest / credit-rating alerts all land on ntfy too. Set up:

    1. Install the ntfy app (Android/iOS) and subscribe to a SECRET topic (treat it like a
       password — anyone with the topic name can read your alerts).
    2. On the server `.env`:  NTFY_TOPIC=your-secret-topic-abc123
       (optional NTFY_SERVER=https://ntfy.sh  ·  NTFY_TOKEN=tk_... for a protected/self-hosted server)

No topic configured → no-op (returns False), so it's safe to leave unset.
"""
from __future__ import annotations

import os

import structlog

log = structlog.get_logger()

# Alert channels → per-channel ntfy topic env var. Each is a separate ntfy topic the user
# subscribes to independently (so they can mute/prioritise). Unset channel topic → falls back
# to NTFY_TOPIC (the catch-all), so partial config still delivers.
_CHANNEL_ENV = {
    "signals": "NTFY_TOPIC_SIGNALS",   # 🔔 swing / result / squeeze trade cards
    "credit": "NTFY_TOPIC_CREDIT",     # 💳 credit / rating actions
    "market": "NTFY_TOPIC_MARKET",     # 📈 moves / surveillance / analyst / ops
    "digest": "NTFY_TOPIC_DIGEST",     # 📊 morning brief / EOD / weekly digest
    "sweep": "NTFY_TOPIC_SWEEP",       # 🌊 Daily Sweep strategy (forward-test, own channel)
}


def _topic_for(channel: str | None) -> str | None:
    if channel:
        t = os.environ.get(_CHANNEL_ENV.get(channel, ""))
        if t:
            return t
    return os.environ.get("NTFY_TOPIC")          # catch-all fallback


def _ascii_title(text: str | None) -> str:
    """ntfy's Title is an HTTP header (latin-1) — strip emoji/non-ASCII from the first line."""
    first = (text or "").splitlines()[0] if text else ""
    t = first.encode("ascii", "ignore").decode().strip()
    return t[:90] or "Stock-Bot"


def ntfy_send(text: str, *, channel: str | None = None, title: str | None = None) -> bool:
    """POST one notification to ntfy on `channel`'s topic (or the catch-all NTFY_TOPIC).
    False (no raise) if no topic configured or the post fails."""
    topic = _topic_for(channel)
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": _ascii_title(title or text)}      # sanitise even an explicit title (header is latin-1)
    tok = os.environ.get("NTFY_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        import requests
        resp = requests.post(f"{server}/{topic}", data=text.encode("utf-8"),
                             headers=headers, timeout=10)
        if resp.status_code >= 300:
            log.warning("ntfy_send_failed", status=resp.status_code)
            return False
        return True
    except Exception:
        log.exception("ntfy_send_error")
        return False
