"""平台身份、会话和业务结果；不导入框架、凭据或旧状态库。"""
from dataclasses import dataclass, field
import json


@dataclass(frozen=True)
class Identity:
    platform: str
    app: str
    kind: str
    opaque_id: str = field(repr=False)
    scope: str = field(default="", repr=False)

    def __post_init__(self):
        if self.platform not in {"onebot11", "qq_official"}:
            raise ValueError("未知平台")
        if self.kind not in {"user", "group", "member"} or not self.app or not self.opaque_id:
            raise ValueError("身份信息不完整")
        if self.kind == "member" and not self.scope:
            raise ValueError("群成员身份必须包含群范围")

    @property
    def key(self):
        # JSON 编码防止 OpenID 中的分隔符造成碰撞；禁止转为数字 QQ 号。
        return json.dumps([self.platform, self.app, self.kind, self.scope, self.opaque_id],
                          ensure_ascii=True, separators=(",", ":"))


@dataclass(frozen=True)
class Request:
    actor: Identity
    conversation: Identity
    event_id: str = field(repr=False)
    text: str = field(repr=False)
    attachments: tuple = field(default=(), repr=False)
    shared_video: str | None = field(default=None, repr=False)
    shared_video_title: str = field(default='', repr=False)
    shared_video_author: str = field(default='', repr=False)
    mentioned_members: tuple[Identity, ...] = field(default=(), repr=False)
    mentions_everyone: bool = False

    @property
    def session(self):
        return (self.actor.key, self.conversation.key)


@dataclass(frozen=True)
class Media:
    kind: str
    data: bytes = field(repr=False)
    name: str = ""

    def __post_init__(self):
        if self.kind not in {"image", "video", "audio", "file"}:
            raise ValueError("未知媒体类型")


@dataclass(frozen=True)
class Result:
    text: str = ""
    images: tuple[bytes, ...] = field(default=(), repr=False)
    file: bytes | None = field(default=None, repr=False)
    filename: str = ""
    media: tuple[Media | str, ...] = field(default=(), repr=False)


def legacy_delivery_status(target, verified_target=None):
    """只做投递决策，不修改旧订阅或把旧 QQ 号用作 OpenID。"""
    if verified_target is None:
        return "paused_identity_unverified"
    if not isinstance(verified_target, Identity) or verified_target.platform != "qq_official":
        return "paused_invalid_target"
    return "pending_platform_permission_and_consent"
