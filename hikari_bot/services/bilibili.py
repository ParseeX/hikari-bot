"""
bilibili.py — B 站专栏（文章）发布工具

依赖：aiohttp（项目已有）
用法：调用 post_article_with_images()
"""

from datetime import datetime, timezone, timedelta

import aiohttp
from nonebot import get_driver

from hikari_bot.core.logger import log_message


def _get_credentials() -> dict[str, str] | None:
    """从 NoneBot 配置读取 B 站 Cookie，任意一项缺失则返回 None。"""
    cfg = get_driver().config
    keys = {
        "sessdata": "bilibili_sessdata",
        "bili_jct": "bilibili_bili_jct",
        "buvid3":   "bilibili_buvid3",
        "dede_uid": "bilibili_dede_user_id",
    }
    creds = {k: getattr(cfg, v, "") or "" for k, v in keys.items()}
    if not all(creds.values()):
        return None
    return creds


async def _upload_image(session: aiohttp.ClientSession, bili_jct: str,
                        img_bytes: bytes) -> str | None:
    """将图片上传到 B 站专栏图床，返回 CDN URL，失败返回 None。"""
    form = aiohttp.FormData()
    form.add_field("file_up", img_bytes, filename="report.png", content_type="image/png")
    form.add_field("csrf", bili_jct)
    try:
        async with session.post(
            "https://api.bilibili.com/x/article/creative/article/upimage",
            data=form,
        ) as resp:
            data = await resp.json()
            if data.get("code") == 0:
                return data["data"]["url"]
            await log_message(f"[bili] Image upload failed: code={data.get('code')} msg={data.get('message')}")
    except Exception as e:
        await log_message(f"[bili] Image upload exception: {e}")
    return None


def _build_article_content(intro: str, img_urls: list[str]) -> tuple[str, int]:
    """
    拼装 B 站专栏 HTML 内容，返回 (content_html, word_count)。
    intro 支持用 \\n 换行，每行转为一个 <p>。
    """
    parts: list[str] = []

    # 开头文字段落
    for line in intro.strip().splitlines():
        line = line.strip()
        if line:
            parts.append(f"<p>{line}</p>")
        else:
            parts.append("<p><br /></p>")

    # 图片
    for url in img_urls:
        parts.append(
            f'<figure class="img-box" contenteditable="false">'
            f'<img src="{url}" />'
            f'<figcaption class="caption"></figcaption>'
            f'</figure>'
        )

    content = "".join(parts)
    # B 站要求传 words（字数），粗略估计
    word_count = len(intro) + len(img_urls) * 10
    return content, word_count


async def post_article_with_images(
    screenshots: list[bytes],
    date_str: str,
    pub_hour: int = 21,
    pub_minute: int = 30,
) -> bool:
    """
    将截图列表发布为 B 站定时专栏文章。

    :param screenshots: 按页顺序的截图字节列表
    :param date_str:    日期字符串，格式 YYYY-MM-DD
    :param pub_hour:    定时发布小时（北京时间，默认 21）
    :param pub_minute:  定时发布分钟（北京时间，默认 30）
    :return:            成功返回 True
    """
    creds = _get_credentials()
    if not creds:
        await log_message("[bili] Credentials not configured, skipping Bilibili post.")
        return False

    sessdata = creds["sessdata"]
    bili_jct = creds["bili_jct"]
    buvid3   = creds["buvid3"]
    dede_uid = creds["dede_uid"]

    cookies = {
        "SESSDATA":   sessdata,
        "bili_jct":   bili_jct,
        "buvid3":     buvid3,
        "DedeUserID": dede_uid,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://member.bilibili.com/",
        "Origin":  "https://member.bilibili.com",
    }

    # 定时发布时间戳（北京时间 = UTC+8）
    beijing_tz = timezone(timedelta(hours=8))
    year, month, day = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    pub_dt = datetime(year, month, day, pub_hour, pub_minute, 0, tzinfo=beijing_tz)
    pub_timestamp = int(pub_dt.timestamp())

    # 文章标题
    title = f"{date_str[:4]}.{date_str[5:7]}.{date_str[8:10]} 日本游戏王卡价日报"

    # 开头固定文字
    intro = (
        "Cardrush 为日本主流卡店平台之一，其公开买取价常被用于观察市场行情。\n"
        "本文基于公开数据整理，仅供交流参考。\n"
        "预计每日 21:30（北京时间）更新。"
    )

    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        # 1. 上传全部图片
        img_urls: list[str] = []
        for i, shot in enumerate(screenshots, 1):
            url = await _upload_image(session, bili_jct, shot)
            if url is None:
                await log_message(f"[bili] Failed to upload page {i}, aborting.")
                return False
            img_urls.append(url)
            await log_message(f"[bili] Uploaded page {i}/{len(screenshots)}: {url}")

        # 2. 组装文章内容
        content, word_count = _build_article_content(intro, img_urls)
        banner_url = img_urls[0]  # 封面用第一张图

        # 3. 创建草稿并设置定时发布
        payload = {
            "aid":         "0",          # 0 = 新建
            "title":       title,
            "banner_url":  banner_url,
            "content":     content,
            "words":       str(word_count),
            "category":    "0",
            "list_id":     "0",
            "tid":         "17",         # 游戏分类
            "original":    "1",
            "csrf":        bili_jct,
            "pub_type":    "2",          # 2 = 定时发布
            "pub_time":    str(pub_timestamp),
        }

        try:
            async with session.post(
                "https://api.bilibili.com/x/article/creative/draft/addupdate",
                data=payload,
            ) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    await log_message(
                        f"[bili] Draft creation failed: code={data.get('code')} msg={data.get('message')}"
                    )
                    return False
                draft_id = data["data"]["aid"]
                await log_message(f"[bili] Draft created: aid={draft_id}")
        except Exception as e:
            await log_message(f"[bili] Draft creation exception: {e}")
            return False

        # 4. 提交草稿（触发审核 + 定时发布）
        submit_payload = {
            "aid":  str(draft_id),
            "csrf": bili_jct,
        }
        try:
            async with session.post(
                "https://api.bilibili.com/x/article/creative/draft/submit",
                data=submit_payload,
            ) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    await log_message(
                        f"[bili] Draft submit failed: code={data.get('code')} msg={data.get('message')}"
                    )
                    return False
                await log_message(
                    f"[bili] Article submitted, scheduled at Beijing {pub_hour:02d}:{pub_minute:02d} "
                    f"({date_str}) pub_timestamp={pub_timestamp}"
                )
                return True
        except Exception as e:
            await log_message(f"[bili] Draft submit exception: {e}")
            return False
