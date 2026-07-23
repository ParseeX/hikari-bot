import base64
from collections.abc import Mapping, Sequence

from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
)

from hikari_bot.core.logger import log_message


def _is_qq_send_timeout(error: Exception) -> bool:
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
    if (user_id is None) == (group_id is None):
        raise ValueError("exactly one QQ forward target is required")

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
        await log_message(
            f"{log_prefix} QQ merged forward: "
            "retcode=1200 ignored."
        )
        return False
    return True
