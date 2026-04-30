"""
bilibili.py — B 站图文动态（多图+文字+定时）发布工具

依赖：bilibili-api-python（服务器安装：pip install bilibili-api-python）
用法：调用 post_article_with_images()
"""

from datetime import datetime, timezone, timedelta

from nonebot import get_driver

from hikari_bot.core.logger import log_message


def _get_credential():
    """从 NoneBot 配置读取 B 站 Cookie，任意一项缺失则返回 None。"""
    try:
        from bilibili_api import Credential
    except ImportError:
        return None

    cfg = get_driver().config
    keys = {
        "sessdata": "bilibili_sessdata",
        "bili_jct": "bilibili_bili_jct",
        "buvid3":   "bilibili_buvid3",
        "dede_uid": "bilibili_dede_user_id",
    }
    vals = {k: getattr(cfg, v, "") or "" for k, v in keys.items()}
    if not all(vals.values()):
        return None

    return Credential(
        sessdata=vals["sessdata"],
        bili_jct=vals["bili_jct"],
        buvid3=vals["buvid3"],
        dedeuserid=vals["dede_uid"],
    )


async def post_article_with_images(
    screenshots: list[bytes],
    date_str: str,
    pub_hour: int = 21,
    pub_minute: int = 30,
) -> bool:
    """
    将截图列表以图文动态形式发布到 B 站（支持定时）。

    :param screenshots: 按页顺序的截图字节列表
    :param date_str:    日期字符串，格式 YYYY-MM-DD
    :param pub_hour:    定时发布小时（北京时间，默认 21）
    :param pub_minute:  定时发布分钟（北京时间，默认 30）
    :return:            成功返回 True
    """
    try:
        from bilibili_api import dynamic
        from bilibili_api.utils.picture import Picture
    except ImportError:
        await log_message("[bili] bilibili-api-python not installed, skipping post.")
        return False

    credential = _get_credential()
    if credential is None:
        await log_message("[bili] Credentials not configured, skipping Bilibili post.")
        return False

    # 固定开头文字（含标题行）
    intro = (
        f"{date_str[:4]}.{date_str[5:7]}.{date_str[8:10]} 日本游戏王卡价日报\n\n"
        "Cardrush 为日本主流卡店平台之一，其公开买取价常被用于观察市场行情。\n"
        "本文基于公开数据整理，仅供交流参考。\n"
        "预计每日 21:30（北京时间）更新。"
    )

    # 定时发布时间（北京时间 = UTC+8）
    beijing_tz = timezone(timedelta(hours=8))
    year, month, day = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    send_time = datetime(year, month, day, pub_hour, pub_minute, 0, tzinfo=beijing_tz)

    try:
        # 将截图字节转为 Picture 对象
        pics = [Picture.from_content(shot, "png") for shot in screenshots]
        await log_message(f"[bili] Prepared {len(pics)} image(s) for upload.")

        # 构建图文动态（含定时）并发送
        build = dynamic.BuildDynamic.create_by_args(
            text=intro,
            pics=pics,
            send_time=send_time,
        )
        result = await dynamic.send_dynamic(build, credential)
        await log_message(
            f"[bili] Dynamic posted, scheduled at Beijing {pub_hour:02d}:{pub_minute:02d} "
            f"({date_str}). Result: {result}"
        )
        return True

    except Exception as e:
        await log_message(f"[bili] Post failed: {e}")
        return False
