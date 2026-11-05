import copy
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

DEFAULT_CONFIG_DICT: Dict[str, Any] = {
    "symbol": "BTCUSDT",
    "interval": "15m",
    "limit": 150,
    "volume_multiplier": 1.5,
    "swing_left": 3,
    "swing_right": 3,
    "oi_check_interval": 30,
    "min_delta_ratio": 0.1,
    "klines_refresh_interval": 120,
    "cooldown_seconds": 900,
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "min_score": 4
    },
    "backtest": {
        "taker_fee_pct": 0.05,
        "maker_fee_pct": 0.02,
        "slippage_pct": 0.02,
        "target_rr": 2.0
    }
}

@dataclass
class BotConfig:
    symbol: str = "BTCUSDT"
    interval: str = "15m"
    limit: int = 150
    volume_multiplier: float = 1.5
    swing_left: int = 3
    swing_right: int = 3
    oi_check_interval: int = 30
    min_delta_ratio: float = 0.1
    klines_refresh_interval: int = 120
    cooldown_seconds: int = 900
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_min_score: int = 4
    taker_fee_pct: float = 0.05
    maker_fee_pct: float = 0.02
    slippage_pct: float = 0.02
    target_rr: float = 2.0

def load_config(config_path: Optional[str] = None) -> BotConfig:
    """
    Loads config safely using deepcopy and merges with optional json file and env vars.
    """
    raw = copy.deepcopy(DEFAULT_CONFIG_DICT)

    if not config_path:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, "config.json")

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_dict = json.load(f)
                for k, v in user_dict.items():
                    if isinstance(v, dict) and k in raw and isinstance(raw[k], dict):
                        raw[k].update(v)
                    else:
                        raw[k] = v
        except Exception as e:
            print(f"[!] Warning reading config: {e}. Falling back to default settings.")

    # Env vars overrides (safe: no tokens exposed on CLI args)
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", raw.get("telegram", {}).get("bot_token", ""))
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", raw.get("telegram", {}).get("chat_id", ""))
    tg_enabled = raw.get("telegram", {}).get("enabled", False)
    if tg_token and tg_chat:
        tg_enabled = True

    # Cooldown default: if 15m -> 900s, if 5m -> 300s, if 1m -> 60s
    interval_str = raw.get("interval", "15m").lower()
    interval_seconds = 900
    if interval_str.endswith("m"):
        interval_seconds = int(interval_str[:-1]) * 60
    elif interval_str.endswith("h"):
        interval_seconds = int(interval_str[:-1]) * 3600

    cooldown = raw.get("cooldown_seconds", interval_seconds)

    bt = raw.get("backtest", {})

    return BotConfig(
        symbol=raw.get("symbol", "BTCUSDT").upper(),
        interval=interval_str,
        limit=int(raw.get("limit", 150)),
        volume_multiplier=float(raw.get("volume_multiplier", 1.5)),
        swing_left=int(raw.get("swing_left", 3)),
        swing_right=int(raw.get("swing_right", 3)),
        oi_check_interval=int(raw.get("oi_check_interval", 30)),
        min_delta_ratio=float(raw.get("min_delta_ratio", 0.1)),
        klines_refresh_interval=int(raw.get("klines_refresh_interval", 120)),
        cooldown_seconds=int(cooldown),
        telegram_enabled=bool(tg_enabled),
        telegram_token=str(tg_token),
        telegram_chat_id=str(tg_chat),
        telegram_min_score=int(raw.get("telegram", {}).get("min_score", 4)),
        taker_fee_pct=float(bt.get("taker_fee_pct", 0.05)),
        maker_fee_pct=float(bt.get("maker_fee_pct", 0.02)),
        slippage_pct=float(bt.get("slippage_pct", 0.02)),
        target_rr=float(bt.get("target_rr", 2.0))
    )
