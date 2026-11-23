"""Справочники из Keitaro для выпадающих списков."""
from fastapi import APIRouter, Depends

from app.keitaro.client import KeitaroClient, get_keitaro

router = APIRouter(prefix="/api/keitaro", tags=["keitaro"])


def _slim(items, *keys):
    return [{k: i.get(k) for k in ("id", *keys)} for i in items]


@router.get("/domains")
async def domains(kc: KeitaroClient = Depends(get_keitaro)):
    return _slim(await kc.domains(), "name")


@router.get("/groups")
async def groups(kc: KeitaroClient = Depends(get_keitaro)):
    return _slim(await kc.groups(), "name")


@router.get("/traffic-sources")
async def traffic_sources(kc: KeitaroClient = Depends(get_keitaro)):
    return _slim(await kc.traffic_sources(), "name")


@router.get("/offers")
async def offers(q: str = "", limit: int = 20, kc: KeitaroClient = Depends(get_keitaro)):
    """Автокомплит: поиск по ID или части названия. Без q - первые `limit` офферов."""
    q = q.strip().lower()
    items = [o for o in await kc.offers()
             if not q or q == str(o.get("id")) or q in (o.get("name") or "").lower()]
    return _slim(items[:limit], "name")
