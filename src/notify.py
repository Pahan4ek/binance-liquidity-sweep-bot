import logging

import requests

from src.models import SweepSignal

logger = logging.getLogger("telegram_notifier")

def send_telegram_alert(
    token: str,
    chat_id: str,
    signal: SweepSignal,
    symbol: str,
    interval: str
) -> bool:
    """
    Sends a formatted HTML notification to Telegram with proper error checking.
    """
    if not token or not chat_id:
        return False

    direction_str = "SHORT &#x2193;" if signal.direction == "short" else "LONG &#x2191;"
    emoji = "🔥" * min(signal.score, 3)

    reasons_html = "".join([f"  • {r}\n" for r in signal.reasons])

    msg = (
        f"<b>{emoji} {signal.direction.upper()} LIQUIDITY SWEEP</b>\n"
        f"<b>Symbol:</b> <code>{symbol}</code> ({interval})\n"
        f"<b>Action:</b> <b>{direction_str}</b>\n"
        f"<b>Swept Level:</b> <code>{signal.level_price:.4f}</code>\n"
        f"<b>Entry:</b> <code>{signal.candle_close:.4f}</code>\n"
        f"<b>Invalidation (SL):</b> <code>{signal.stop_loss:.4f}</code>\n"
        f"<b>Confluence Score:</b> <b>{signal.score}/6</b>\n\n"
        f"<b>Signals & Confirmations:</b>\n{reasons_html}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        resp = requests.post(url, json=payload, timeout=8)
        data = resp.json()
        if not resp.ok or not data.get("ok"):
            logger.warning("Telegram alert failed. HTTP %s: %s", resp.status_code,
                           data.get("description", "Unknown error"))
            return False
        return True
    except Exception as e:
        logger.error("Exception sending Telegram alert: %s", e)
        return False
