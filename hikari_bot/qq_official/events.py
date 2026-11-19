"""官方事件转换与入口去重；只持有有界内存，不记录消息正文。"""
from collections import OrderedDict
import time

from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from hikari_bot.services.platform import Identity, Request
from .shared_video import shared_video, shared_title, shared_author


def request_from_event(bot, event):
    app = str(bot.self_id)
    if isinstance(event, GroupMessageCreateEvent):
        actor = Identity("qq_official", app, "member", event.get_user_id(), event.group_openid)
        conversation = Identity("qq_official", app, "group", event.group_openid)
    elif isinstance(event, C2CMessageCreateEvent):
        actor = conversation = Identity("qq_official", app, "user", event.get_user_id())
    else:
        raise ValueError("不支持此事件")
    return Request(actor, conversation, event.id, event.get_plaintext(),
                   tuple(event.attachments or ()),
                   getattr(event, '_hikari_shared_video', shared_video(event)),
                   getattr(event, '_hikari_shared_video_title', shared_title(event)),
                   getattr(event, '_hikari_shared_video_author', shared_author(event)))


class EventGate:
    def __init__(self, *, clock=time.monotonic, limit=4096):
        self.clock, self.limit = clock, limit
        self.seen = OrderedDict()

    def claim(self, request):
        now = self.clock()
        for key in [key for key, expiry in self.seen.items() if expiry <= now]:
            self.seen.pop(key, None)
        key = (*request.session, request.event_id)
        if not request.event_id or key in self.seen:
            return False
        # 不在任何 await 后登记，防止并发重复事件重复执行业务写入。
        if len(self.seen) >= self.limit:
            return False
        self.seen[key] = now + 3600
        return True
