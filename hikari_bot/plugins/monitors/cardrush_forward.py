"""OneBot 的 Cardrush 一层合并转发适配器。

每张日报图片对应一个转发节点，最终只调用一次私聊或群聊转发接口，
不再依赖中转群，也不把多个页面拆成多条独立消息。
"""

import base64
from collections.abc import Mapping, Sequence

from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
)

from hikari_bot.core.logger import log_message


def _is_qq_send_timeout(error: Exception) -> bool:
    """兼容 OneBot 错误对象的两种 retcode 存放位置。"""
    info = getattr(error, "info", None)
    retcode = (
        info.get("retcode")
        if isinstance(info, Mapping)
        else getattr(error, "retcode", None)
    )
    return retcode == 1200


async def send_qq_forward(
    bot: Bot,
    pages: Sequence[bytes],
    *,
    user_id: int | None = None,
    group_id: int | None = None,
    log_prefix: str,
) -> bool:
    """把页面组成一次合并转发；1200 表示结果未知，避免重试造成重复消息。"""
    if (user_id is None) == (group_id is None):
        raise ValueError("exactly one QQ forward target is required")

    # 一个页面一个节点，节点内容只包含图片，保持手机端阅读顺序。
    total = len(pages)
    nodes = []
    for index, page in enumerate(pages, 1):
        encoded = base64.b64encode(page).decode()
        nodes.append(
            MessageSegment.node_custom(
                user_id=int(bot.self_id),
                nickname=f"Cardrush 图报 {index}/{total}",
                content=Message(
                    MessageSegment.image(f"base64://{encoded}")
                ),
            )
        )

    try:
        # 目标必须二选一，且整个日报只发送一次 API 请求。
        if group_id is not None:
            await bot.call_api(
                "send_group_forward_msg",
                group_id=group_id,
                messages=nodes,
            )
        else:
            await bot.call_api(
                "send_private_forward_msg",
                user_id=user_id,
                messages=nodes,
            )
    except Exception as error:
        if not _is_qq_send_timeout(error):
            raise
        # QQ 可能已经收到了消息；这里只记录并返回，不再重发同一批图片。
        await log_message(
            f"{log_prefix} QQ merged forward: "
            "retcode=1200 ignored."
        )
        return False
    return True
