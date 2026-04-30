"""
bilibili.py — B 站专栏（图文）发布工具

依赖：
  pip install bilibili-api-python httpx

Cookie 来源：data/bili_auth.json（由服务器上运行 bili_login.py 扫码登录生成）

发布流程：
  1. 用 bilibili_api.Picture 上传截图，拿到 CDN URL
  2. 组装 HTML 正文（图片用 <figure class="img-box"> 内嵌）
  3. POST /x/article/creative/draft/addupdate  保存草稿（含定时）
  4. POST /x/article/creative/draft/publish    发布草稿
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
    将截图列表以专栏形式发布到 B 站（标题独立、图片内嵌正文、支持定时）。

    :param screenshots: 按页顺序的截图字节列表
    :param date_str:    日期字符串，格式 YYYY-MM-DD
    :param pub_hour:    定时发布小时（北京时间，默认 21）
    :param pub_minute:  定时发布分钟（北京时间，默认 30）
    :return:            成功返回 True
    """
    try:
        import httpx
    except ImportError as e:
        await log_message(f"[bili] 缺少依赖（{e}），请 pip install httpx。"); return False

    credential = _get_credential()
    if credential is None:
        await log_message(
            f"[bili] 未找到凭据，请先在服务器上运行 bili_login.py 完成扫码登录。\n"
            f"  预期文件路径：{_AUTH_FILE}"
        )
        return False

    # 预检：验证 cookie 是否有效
    try:
        is_login = await credential.check_valid()
    except Exception as e:
        is_login = False
        await log_message(f"[bili] check_valid() error: {e}")
    if not is_login:
        await log_message(
            "[bili] 账号未登录（SESSDATA 无效或已过期）。\n"
            "请重新在服务器上运行 bili_login.py 重新扫码登录。"
        )
        return False

    date_label = f"{date_str[:4]}.{date_str[5:7]}.{date_str[8:10]}"
    title = f"{date_label} 日本游戏王卡价日报"

    beijing_tz = timezone(timedelta(hours=8))
    year, month, day = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    send_time = datetime(year, month, day, pub_hour, pub_minute, 0, tzinfo=beijing_tz)
    pub_timestamp = int(send_time.timestamp())

    try:
        cookies = {
            "SESSDATA": credential.sessdata,
            "bili_jct": credential.bili_jct,
            "buvid3": credential.buvid3 or "",
            "DedeUserID": str(credential.dedeuserid or ""),
        }
        if credential.buvid4:
            cookies["buvid4"] = credential.buvid4

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://member.bilibili.com/platform/upload/text/edit",
            "Origin": "https://member.bilibili.com",
        }

        headers_img = headers.copy()
        headers_img.update({
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://member.bilibili.com",
        })

        async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=30) as client:
            # 专栏专用图片上传接口
            img_urls = []
            await log_message(f"[bili] Uploading {len(screenshots)} image(s) via article API...")
            for idx, img_bytes in enumerate(screenshots):
                files = {'file': (f'image{idx}.png', img_bytes, 'image/png')}
                resp = await client.post(
                    "https://api.bilibili.com/x/article/creative/image/upload",
                    files=files,
                    data={"biz": "article", "csrf": credential.bili_jct},
                    headers=headers_img,
                )
                raw = resp.text.strip()
                await log_message(f"[bili] Image upload resp ({resp.status_code}): {raw[:300]}")
                if not raw:
                    await log_message(f"[bili] Image upload failed: empty response, HTTP {resp.status_code}"); return False
                try:
                    j = resp.json()
                except Exception as e:
                    await log_message(f"[bili] Image upload JSON error: {e}, raw={raw[:300]}"); return False
                if j.get("code") != 0 or "url" not in j.get("data", {}):
                    await log_message(f"[bili] Image upload failed: {j}"); return False
                img_urls.append(j["data"]["url"])
            await log_message(f"[bili] Article image URLs: {img_urls}")

            cover_url = img_urls[0] if img_urls else ""

            desc_html = (
                "<p>Cardrush 为日本主流卡店平台之一，其公开买取价常被用于观察市场行情。</p>"
                "<p>本文基于公开数据整理，仅供交流参考。</p>"
                "<p>预计每日 21:30（北京时间）更新。</p>"
            )
            def _img_url(url: str) -> str:
                return url.replace("https:", "").replace("http:", "")
            images_html = "".join(
                f'<figure class="img-box">'
                f'<img data-src="{_img_url(url)}" class="" style="cursor: zoom-in;">'
                f'<figcaption class=""></figcaption>'
                f'</figure>'
                for url in img_urls
            )
            content_html = desc_html + images_html

            # Step 1：保存草稿
            draft_resp = await client.post(
                "https://api.bilibili.com/x/article/creative/draft/addupdate",
                data={
                    "title": title,
                    "content": content_html,
                    "cover": _img_url(cover_url),
                    "category": 0,
                    "list_id": 0,
                    "tid": 4,
                    "original": 1,
                    "csrf": credential.bili_jct,
                },
            )
            draft_json = draft_resp.json()
            if draft_json.get("code") != 0:
                await log_message(f"[bili] Draft save failed: {draft_json}"); return False
            aid = draft_json["data"]["aid"]
            await log_message(f"[bili] Draft saved (aid={aid}).")

            # Step 2：提交发布（定时时间戳在这里传入）
            pub_resp = await client.post(
                "https://api.bilibili.com/x/article/creative/draft/publish",
                data={"aid": aid, "pub_time": pub_timestamp, "csrf": credential.bili_jct},
            )
            raw = pub_resp.text.strip()
            await log_message(f"[bili] Publish response ({pub_resp.status_code}): {raw[:500]}")
            if not raw:
                if pub_resp.status_code in (200, 204):
                    await log_message("[bili] Publish response empty but status OK, treating as success.")
                else:
                    await log_message(f"[bili] Publish failed: empty response, HTTP {pub_resp.status_code}"); return False
            else:
                pub_json = pub_resp.json()
                if pub_json.get("code") != 0:
                    await log_message(f"[bili] Publish failed: {pub_json}"); return False
            await log_message(
                f"[bili] Article posted (cv{aid}), "
                f"scheduled at Beijing {pub_hour:02d}:{pub_minute:02d} ({date_str})."
            )
            return True

    except Exception as e:
        import traceback
        await log_message(f"[bili] Post failed: {e}\n{traceback.format_exc()}")
        return False
