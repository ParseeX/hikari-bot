"""通过实际卡价请求检查连接，不写入价格数据库。"""

import asyncio
from time import perf_counter

import requests

from .client import CardrushClient


async def check_connection(client: CardrushClient) -> str:
    started = perf_counter()
    try:
        records = await asyncio.to_thread(client.query, limit=1, page=1)
        result = "成功：已读取卡价数据" if records else "Failed：未读取到卡价数据"
    except Exception as error:
        # 不输出原始异常，代理 URL 或认证信息可能出现在异常文本中。
        cause = error.__cause__ or error
        if isinstance(cause, requests.exceptions.ProxyError):
            reason = "代理连接失败"
        elif isinstance(cause, requests.exceptions.Timeout):
            reason = "请求超时"
        elif isinstance(cause, requests.exceptions.HTTPError):
            status = cause.response.status_code if cause.response is not None else "未知"
            reason = f"HTTP {status}"
        elif isinstance(cause, requests.exceptions.ConnectionError):
            reason = "网络连接失败"
        else:
            reason = "请求或卡价数据解析失败"
        result = f"Failed：{reason}"
    return f"Cardrush 连接测试{result}（耗时 {perf_counter() - started:.2f} 秒）"
