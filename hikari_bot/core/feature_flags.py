import json
import os

from hikari_bot.core.constants import RESOURCES_DIR
from hikari_bot.core.logger import log_message

FLAGS_FILE = os.path.join(RESOURCES_DIR, "feature_flags.json")

async def _load_flags() -> dict:
    try:
        with open(FLAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        await log_message(f"[feature_flags] Exception occurred while loading flags: {e}")
        return {}

async def _save_flags(flags: dict) -> None:
    os.makedirs(RESOURCES_DIR, exist_ok=True)
    with open(FLAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(flags, f, ensure_ascii=False, indent=2)

async def get_notify_enabled() -> bool:
    flags = await _load_flags()
    return flags.get("mycard_notify", True)

async def set_notify_enabled(value: bool) -> None:
    flags = await _load_flags()
    flags["mycard_notify"] = bool(value)
    await _save_flags(flags)