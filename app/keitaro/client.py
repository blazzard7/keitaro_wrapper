"""
Клиент Keitaro Admin API v1.

Правило безопасности: любой метод записи проходит через _request(write=True),
который блокируется, пока KEITARO_WRITE_ENABLED=false.
Все запросы чтения - GET.
"""
import httpx

from app.config import settings
from app.errors import KeitaroError, WriteDisabledError


class KeitaroClient:
    def __init__(self, base_url: str, api_key: str, write_enabled: bool = False,
                 timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.write_enabled = write_enabled
        self.timeout = timeout
        self._transport = transport  # для тестов (httpx.MockTransport)

    async def _request(self, method: str, path: str, *, params=None, json=None, write=False):
        if write and not self.write_enabled:
            raise WriteDisabledError(
                "Запись в Keitaro выключена (KEITARO_WRITE_ENABLED=false в .env)."
            )
        try:
            async with httpx.AsyncClient(
                headers={"Api-Key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout, transport=self._transport,
            ) as c:
                r = await c.request(method, self.base_url + path, params=params, json=json)
        except httpx.HTTPError as e:
            raise KeitaroError(f"Keitaro недоступен: {e}") from e
        if r.status_code >= 400:
            raise KeitaroError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None

    # ---- чтение ----
    async def domains(self) -> list[dict]:
        return await self._request("GET", "/domains")

    async def groups(self) -> list[dict]:
        items = await self._request("GET", "/groups")
        # у групп есть тип (campaigns/offers/...); если поля нет - не фильтруем
        return [g for g in items if g.get("type", "campaigns") == "campaigns"]

    async def traffic_sources(self) -> list[dict]:
        return await self._request("GET", "/traffic_sources")

    async def offers(self) -> list[dict]:
        return await self._request("GET", "/offers")

    async def get_campaign(self, campaign_id: int) -> dict:
        return await self._request("GET", f"/campaigns/{campaign_id}")

    async def campaign_streams(self, campaign_id: int) -> list[dict]:
        return await self._request("GET", f"/campaigns/{campaign_id}/streams")

    # ---- запись (только при KEITARO_WRITE_ENABLED=true) ----
    async def create_campaign(self, payload: dict) -> dict:
        return await self._request("POST", "/campaigns", json=payload, write=True)

    async def create_stream(self, payload: dict) -> dict:
        return await self._request("POST", "/streams", json=payload, write=True)

    async def update_stream(self, stream_id: int, payload: dict) -> dict:
        return await self._request("PUT", f"/streams/{stream_id}", json=payload, write=True)


def get_keitaro() -> KeitaroClient:
    """FastAPI-зависимость (в тестах подменяется через dependency_overrides)."""
    return KeitaroClient(settings.keitaro_base_url, settings.keitaro_api_key,
                         write_enabled=settings.keitaro_write_enabled)
