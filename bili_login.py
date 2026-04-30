"""
bili_login.py — 在服务器上扫码登录 B 站，保存 Cookie 到 data/bili_auth.json

使用方法：
  python bili_login.py

运行后会在终端显示二维码（ASCII），用 B 站 App 扫码确认即可。
Cookie 保存到 data/bili_auth.json，bot 运行时自动读取，无需手动填写 .env。

依赖：
  pip install bilibili-api-python qrcode-terminal
"""

import asyncio
import json
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
AUTH_FILE = os.path.join(DATA_DIR, "bili_auth.json")


async def main():
    try:
        from bilibili_api import login_v2
    except ImportError:
        print("请先安装依赖：pip install bilibili-api-python qrcode-terminal")
        sys.exit(1)

    print("正在生成二维码，请用 B 站 App 扫码登录……\n")

    # 生成二维码登录实例
    qr = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)
    await qr.generate_qrcode()

    # 在终端打印二维码（库内部已生成 ASCII 字符形式）
    qr_link = qr._QrCodeLogin__qr_link
    qr_terminal = qr._QrCodeLogin__qr_terminal
    if qr_terminal:
        print(qr_terminal)
    print(f"或访问此链接（用浏览器/App 扫描）：\n{qr_link}\n")

    print("等待扫码……")

    # 轮询登录状态
    credential = None
    while True:
        await asyncio.sleep(2)
        state = await qr.check_state()

        if state == login_v2.QrCodeLoginEvents.SCAN:
            print("已扫码，等待确认……")
        elif state == login_v2.QrCodeLoginEvents.CONF:
            print("扫码成功！正在获取凭据……")
        elif state == login_v2.QrCodeLoginEvents.DONE:
            credential = qr.get_credential()
            break
        elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
            print("二维码已超时，请重新运行脚本。")
            sys.exit(1)

    # 保存 Cookie 到 JSON 文件
    os.makedirs(DATA_DIR, exist_ok=True)
    auth_data = {
        "sessdata":      credential.sessdata,
        "bili_jct":      credential.bili_jct,
        "buvid3":        credential.buvid3,
        "buvid4":        credential.buvid4,
        "dedeuserid":    credential.dedeuserid,
        "ac_time_value": credential.ac_time_value,
    }
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(auth_data, f, ensure_ascii=False, indent=2)

    print(f"\n登录成功！Cookie 已保存到：{AUTH_FILE}")
    print("直接启动 bot，发动态时会自动读取此文件，无需配置 .env。")


if __name__ == "__main__":
    asyncio.run(main())
