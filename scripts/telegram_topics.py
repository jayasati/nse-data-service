"""Discover the supergroup chat_id + each topic's message_thread_id, and emit the
TELEGRAM_* env lines to wire the bot's per-stream routing.

The dispatcher already routes by topic (_topic_id reads TELEGRAM_TOPIC_<NAME>);
this just resolves the ids after you've set up the community.

ONE-TIME SETUP (in the Telegram app — only you can do this):
  1. Create a group, open its settings, and turn ON "Topics" (forum mode).
  2. Add this bot to the group and make it an Admin.
  3. Create your topics: Intraday, Swing, Earnings, Analyst, Why-it-moved, Health.
  4. Post any short message inside EACH topic (e.g. type the topic's name).
Then run this:

    PYTHONPATH=src .venv/bin/python scripts/telegram_topics.py

It prints the supergroup chat_id, every topic's thread id, and ready-to-paste
.env lines (TELEGRAM_CHAT_ID + TELEGRAM_TOPIC_*).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

# topic-name keyword -> TELEGRAM_TOPIC_<SUFFIX> the code reads
_NAME_TO_ENV = [
    (("intraday",), "INTRADAY"),
    (("swing", "positional"), "SWING"),
    (("earning", "result"), "EARNINGS"),
    (("analyst", "rating", "broker"), "ANALYST"),
    (("why", "moved", "digest"), "DIGEST"),
    (("health", "ops", "status", "system"), "HEALTH"),
]


def _env_for(name: str) -> str | None:
    low = (name or "").lower()
    for keys, suffix in _NAME_TO_ENV:
        if any(k in low for k in keys):
            return f"TELEGRAM_TOPIC_{suffix}"
    return None


def main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("TELEGRAM_TOKEN not set in .env"); return 2

    import httpx
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates",
                  params={"limit": 100}, timeout=20)
    r.raise_for_status()
    updates = r.json().get("result", [])
    if not updates:
        print("No recent updates. Post a message in each topic (and ensure the bot\n"
              "is an admin with no webhook set), then re-run."); return 0

    chats: dict[int, str] = {}
    threads: dict[tuple[int, int], str] = {}      # (chat_id, thread_id) -> name
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        chats[cid] = chat.get("title") or chat.get("type") or "?"
        tid = msg.get("message_thread_id")
        if tid is None:
            continue
        # prefer the topic's creation name; else fall back to the message text
        name = (msg.get("forum_topic_created") or {}).get("name") or msg.get("text") or ""
        prev = threads.get((cid, tid), "")
        if name and (not prev or msg.get("forum_topic_created")):
            threads[(cid, tid)] = name.strip()

    print("=== chats the bot has seen ===")
    for cid, title in chats.items():
        kind = "supergroup/forum" if str(cid).startswith("-100") else "private/DM"
        print(f"  {cid}  [{kind}]  {title}")

    # pick the forum supergroup if present
    supergroups = [cid for cid in chats if str(cid).startswith("-100")]
    print("\n=== topics found ===")
    env_lines: list[str] = []
    target = supergroups[0] if supergroups else None
    if target:
        env_lines.append(f"TELEGRAM_CHAT_ID={target}")
    for (cid, tid), name in sorted(threads.items()):
        env = _env_for(name)
        tag = f" -> {env}" if env else "  (no stream match — name it intraday/swing/earnings/analyst/why-moved/health)"
        print(f"  chat {cid}  thread {tid}  '{name}'{tag}")
        if env and cid == target:
            env_lines.append(f"{env}={tid}")

    print("\n=== paste into .env (then restart services / re-run digest) ===")
    print("\n".join(env_lines) if env_lines else "(no supergroup/topics resolved yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
