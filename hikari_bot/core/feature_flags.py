import json
import os

from hikari_bot.core.constants import *

FLAGS_FILE = os.path.join(RESOURCES_DIR, "feature_flags.json")

def _load_flags() -> dict:
    try:
        with open(FLAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_flags(flags: dict) -> None:
    os.makedirs(RESOURCES_DIR, exist_ok=True)
    with open(FLAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(flags, f, ensure_ascii=False, indent=2)

def get_notify_enabled() -> bool:
    flags = _load_flags()
    return flags.get("mycard_notify", True)

def set_notify_enabled(value: bool) -> None:
    flags = _load_flags()
    flags["mycard_notify"] = bool(value)
    _save_flags(flags)