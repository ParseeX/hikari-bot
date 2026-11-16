"""Process-environment configuration only; values are never logged."""
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Mapping


def _ids(env: Mapping[str, str], name: str) -> frozenset[str]:
    try:
        values = json.loads(env.get(name, "[]"))
    except (ValueError, TypeError):
        raise ValueError(f"{name} must be a JSON array of strings") from None
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(f"{name} must be a JSON array of nonempty strings")
    return frozenset(v.strip() for v in values)


@dataclass(frozen=True)
class PilotConfig:
    app_id: str = field(repr=False)
    secret: str = field(repr=False)
    token: str = field(default="", repr=False)
    sandbox: bool = False
    users: frozenset[str] = field(default_factory=frozenset, repr=False)
    groups: frozenset[str] = field(default_factory=frozenset, repr=False)
    catalog_path: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, pairing: bool = False):
        if env.get("HIKARI_QQ_ENABLED", "").lower() != "true":
            raise ValueError("Official QQ pilot is disabled; HIKARI_QQ_ENABLED must be true")
        for key in ("HIKARI_QQ_APP_ID", "HIKARI_QQ_APP_SECRET"):
            if not env.get(key, "").strip():
                raise ValueError(f"{key} is required; enter it privately outside chat")
        sandbox = env.get("HIKARI_QQ_SANDBOX", "").lower()
        if sandbox not in {"true", "false"}:
            raise ValueError("HIKARI_QQ_SANDBOX must be true or false")
        if pairing:
            # Discovery must not consult business paths or existing permissions.
            return cls(env["HIKARI_QQ_APP_ID"].strip(), env["HIKARI_QQ_APP_SECRET"],
                       env.get("HIKARI_QQ_TOKEN", ""), sandbox == "true")
        users = _ids(env, "HIKARI_QQ_ALLOWED_USERS")
        groups = _ids(env, "HIKARI_QQ_ALLOWED_GROUPS")
        if not users and not groups:
            raise ValueError("Configure at least one allowed official QQ user/group identifier")
        path = env.get("HIKARI_QQ_CARD_CATALOG", "").strip()
        if path and not Path(path).is_file():
            raise ValueError("HIKARI_QQ_CARD_CATALOG must point to an existing card catalog")
        return cls(env["HIKARI_QQ_APP_ID"].strip(), env["HIKARI_QQ_APP_SECRET"],
                   env.get("HIKARI_QQ_TOKEN", ""), sandbox == "true", users, groups,
                   Path(path) if path else None)

    def allows(self, kind: str, user_id: str, group_id: str = "") -> bool:
        if kind == "group_at":
            return bool(group_id) and group_id in self.groups
        return kind == "c2c" and user_id in self.users

    def bot_info(self):
        # adapter-qq 1.7.1 requires the token field; C2C/group auth uses secret.
        return {"id": self.app_id, "secret": self.secret, "token": self.token,
                "use_websocket": True, "intent": {
                    "guilds": False, "guild_members": False,
                    "guild_messages": False, "guild_message_reactions": False,
                    "direct_message": False, "open_forum_event": False,
                    "audio_live_member": False,
                    "c2c_group_at_messages": True, "interaction": True,
                    "message_audit": False, "forum_event": False,
                    "audio_action": False, "at_messages": False}}
