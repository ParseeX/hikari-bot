"""
bilibili.py — B 站图文动态（多图+文字+定时）发布工具

依赖：bilibili-api-python（服务器安装：pip install bilibili-api-python）

Cookie 来源：data/bili_auth.json（由服务器上运行 bili_login.py 扫码登录生成）
"""

import json
import os
from datetime import datetime, timezone, timedelta

from hikari_bot.core.logger import log_message

# auth.json 路径：<项目根>/data/bili_auth.json
_AUTH_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "bili_auth.json"
)


def _get_credential():
    """从 data/bili_auth.json 读取 B 站凭据（由 bili_login.py 扫码登录生成）。"""
    try:
        from bilibili_api import Credential
    except ImportError:
        return None

    if not os.path.exists(_AUTH_FILE):
        return None

    try:
        with open(_AUTH_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None

    if not d.get("sessdata") or not d.get("bili_jct"):
        return None

    return Credential(
        sessdata=d.get("sessdata"),
        bili_jct=d.get("bili_jct"),
        buvid3=d.get("buvid3"),
        buvid4=d.get("buvid4"),
        dedeuserid=str(d.get("dedeuserid") or ""),
        ac_time_value=d.get("ac_time_value"),
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
        await log_message(
            f"[bili] 未找到凭据，请先在服务器上运行 bili_login.py 完成扫码登录。\n"
            f"  预期文件路径：{_AUTH_FILE}"
        )
        return False

    # 诊断：把读取到的凭据关键字段输出到 log，确认来源和内容是否正确
    # 敏感字段（SESSDATA/bili_jct）只显示前8位，避免明文泄露
    def _mask(s: str | None, keep: int = 8) -> str:
        if not s:
            return "(empty)"
        return s[:keep] + "…" if len(s) > keep else s

    cred_source = "auth.json" if os.path.exists(_AUTH_FILE) else ".env"
    await log_message(
        f"[bili] Credential debug (source: {cred_source}):\n"
        f"  SESSDATA       = {_mask(credential.sessdata)}\n"
        f"  bili_jct       = {_mask(credential.bili_jct)}\n"
        f"  buvid3         = {_mask(credential.buvid3)}\n"
        f"  DedeUserID     = {credential.dedeuserid}\n"
        f"  buvid4         = {_mask(credential.buvid4)}\n"
        f"  ac_time_value  = {_mask(credential.ac_time_value)}"
    )

    # 预检：验证 cookie 是否有效，-101 时提前报错，避免浪费图片上传流量
    try:
        is_login = await credential.check_valid()
    except Exception as e:
        is_login = False
        await log_message(f"[bili] check_valid() error: {e}")
    if not is_login:
        await log_message(
            "[bili] 账号未登录（SESSDATA 无效或已过期）。\n"
            "请重新从浏览器复制最新 Cookie，更新服务器 .env 中的：\n"
            "  BILIBILI_SESSDATA / BILIBILI_BILI_JCT / BILIBILI_BUVID3 / BILIBILI_DEDE_USER_ID\n"
            "如果拥有 buvid4 和 ac_time_value 也请一并填入。"
        )
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
        import traceback
        await log_message(f"[bili] Post failed: {e}\n{traceback.format_exc()}")
        return False
