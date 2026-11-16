"""NAS 电源服务客户端；身份权限由各平台入口检查。"""
import httpx


async def request_action(base_url, token, action, *, timeout=10, client=None):
    if action not in {'wake', 'shutdown'} or not base_url or not token:
        raise ValueError('电源服务配置或操作无效')
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            return await request_action(base_url, token, action, timeout=timeout, client=owned)
    base_url = base_url.rstrip('/')
    response = await client.post(f"{base_url}/v1/{action}", headers={'Authorization': f'Bearer {token}'})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get('ok') is not True or payload.get('action') != action:
        raise RuntimeError('NAS power service returned an unexpected response')
