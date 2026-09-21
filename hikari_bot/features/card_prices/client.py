"""只访问配置好的本机手机桥接服务，不接收或导出微信凭据。"""
import httpx

from .models import CardVersion, JhsPrice


class JhsUnavailable(RuntimeError):
    pass


class JhsClient:
    def __init__(self, base_url: str, token: str, timeout: float = 90):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def _post(self, path: str, payload: dict) -> dict:
        if not self.base_url or not self.token:
            raise JhsUnavailable("集换社查询服务未配置")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                response = await client.post(
                    self.base_url + path, json=payload,
                    headers={"Authorization": "Bearer " + self.token},
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("error"):
                    raise ValueError("invalid bridge response")
                return data
        except (httpx.HTTPError, ValueError) as exc:
            raise JhsUnavailable("集换社暂时不可用，请稍后重试") from exc

    async def versions(self, name_jp: str) -> list[CardVersion]:
        data = await self._post("/v1/versions", {"name_jp": name_jp})
        try:
            return [CardVersion.from_dict(item) for item in data["versions"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise JhsUnavailable("集换社版本数据无效") from exc

    async def prices(self, version_ids: list[int]) -> dict[int, JhsPrice]:
        if len(version_ids) > 20:
            result: dict[int, JhsPrice] = {}
            for start in range(0, len(version_ids), 20):
                result.update(await self.prices(version_ids[start:start + 20]))
            return result
        data = await self._post("/v1/prices", {"version_ids": version_ids})
        try:
            prices = [JhsPrice.from_dict(item) for item in data["prices"]]
            if {p.id for p in prices} != set(version_ids) or len(prices) != len(set(version_ids)):
                raise ValueError("version mismatch")
            return {p.id: p for p in prices}
        except (KeyError, TypeError, ValueError) as exc:
            raise JhsUnavailable("集换社价格版本不匹配") from exc
