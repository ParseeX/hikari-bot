"""裁定服务的无平台客户端；地址和超时由入口注入。"""
import httpx


async def conclusion(question, url, *, timeout=110, client=None):
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            return await conclusion(question, url, client=owned)
    response = await client.post(url, json={"question": question})
    response.raise_for_status()
    data = response.json()
    answer = data.get("shortAnswer") if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Ruling Assistant response does not contain shortAnswer")
    return answer.strip()
