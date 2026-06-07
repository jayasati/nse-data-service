"""
Send a one-off test message to Telegram to verify the bot wiring (task 5.10).

    python scripts/send_test_alert.py

Reads TELEGRAM_TOKEN / TELEGRAM_CHAT_ID from the environment / .env (same as
bot/dispatcher.py) and posts a sample alert. Use this during setup to confirm
the token + chat id work, without waiting for a real signal during market hours.

Exit codes: 0 = sent, 1 = not configured or send failed.
"""

from __future__ import annotations

from nse_data.bot.dispatcher import format_message, load_telegram_config, send_telegram


def main() -> int:
    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — add them to .env first.")
        return 1

    sample_signal = {
        "symbol": "TESTSTOCK",
        "signal_type": "long_buildup",
        "price": 1000.0,
        "atr_14_daily": 12.0,
        "oi_change_pct": 5.4,
        "price_change_pct": 1.8,
        "volume_ratio": 2.3,
    }
    sample_context = {
        "price_vs_vwap": "above",
        "vwap_slope": 0.5,
        "rsi_5m": 58.0,
        "trend_regime": "uptrend",
    }
    text = "🧪 NSE bot test alert\n\n" + format_message(sample_signal, sample_context, 0.72)

    if send_telegram(token, chat_id, text):
        print(f"Sent test alert to chat {chat_id}. Check Telegram.")
        return 0
    print("Send failed — see the logged error above (bad token/chat id?).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
