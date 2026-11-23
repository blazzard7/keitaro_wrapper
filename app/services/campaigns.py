"""Создание кампании в Keitaro + локальная запись."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import DomainError, KeitaroError
from app.keitaro.client import KeitaroClient
from app.models import Campaign
from app.services.sync import sync_campaign

REDIRECT_URL = "https://www.google.com/"
# Схема потока с HTTP-редиректом и схема потока с офферами. Если в вашем Keitaro названия
# иные - поменять нужно только здесь (проверьте в Swagger: POST /streams).
GEO_STREAM_SCHEMA = "action"
OFFERS_STREAM_SCHEMA = "landings"


def geo_stream_payload(campaign_id: int, geo: str) -> dict:
    return {
        "campaign_id": campaign_id, "name": f"GEO {geo}", "type": "regular",
        "schema": GEO_STREAM_SCHEMA, "position": 1, "state": "active",
        "action_type": "http", "action_options": {"url": REDIRECT_URL},
        "filters": [{"name": "country", "mode": "accept", "payload": [geo]}],
    }


def default_stream_payload(campaign_id: int, keitaro_offer_id: int) -> dict:
    return {
        "campaign_id": campaign_id, "name": "Other GEO", "type": "default",
        "schema": OFFERS_STREAM_SCHEMA, "position": 2, "state": "active",
        "offers": [{"offer_id": keitaro_offer_id, "share": 100, "state": "active"}],
    }


def _require(items: list[dict], item_id: int, label: str) -> None:
    if item_id not in {i["id"] for i in items}:
        raise DomainError(f"{label} с ID {item_id} не найден в Keitaro")


async def create_campaign(db: Session, kc: KeitaroClient, *, name: str, geo: str, offer_id: int,
                          domain_id: int, group_id: int, traffic_source_id: int) -> Campaign:
    geo = geo.upper()
    # 1. Проверяем все ссылки ДО записи, чтобы не создавать полу-кампании по опечатке.
    _require(await kc.offers(), offer_id, "Оффер")
    _require(await kc.domains(), domain_id, "Домен")
    _require(await kc.groups(), group_id, "Группа")
    _require(await kc.traffic_sources(), traffic_source_id, "Источник трафика")

    # 2. Кампания
    remote = await kc.create_campaign({
        "name": name, "type": "position", "state": "active",
        "domain_id": domain_id, "group_id": group_id, "traffic_source_id": traffic_source_id,
    })
    cid = remote["id"]
    camp = Campaign(keitaro_campaign_id=cid, name=name, alias=remote.get("alias"), geo=geo)
    db.add(camp)
    db.commit()  # фиксируем сразу: кампания в Keitaro уже есть, терять её нельзя

    # 3. Потоки. В Keitaro допустим один default-поток: если он уже есть - обновляем, а не создаём.
    try:
        existing = await kc.campaign_streams(cid)
        await kc.create_stream(geo_stream_payload(cid, geo))
        default = next((s for s in existing if s.get("type") == "default"), None)
        payload = default_stream_payload(cid, offer_id)
        if default:
            await kc.update_stream(default["id"], payload)
        else:
            await kc.create_stream(payload)
    except KeitaroError as e:
        raise KeitaroError(
            f"Кампания #{cid} создана, но потоки созданы не полностью: {e}. "
            f"Откройте кампанию в обёртке (она подтянет фактическое состояние) и проверьте потоки."
        ) from e

    return await sync_campaign(db, kc, camp)


async def import_campaign(db: Session, kc: KeitaroClient, keitaro_campaign_id: int) -> Campaign:
    camp = db.scalar(select(Campaign).where(Campaign.keitaro_campaign_id == keitaro_campaign_id))
    if camp is None:
        remote = await kc.get_campaign(keitaro_campaign_id)
        camp = Campaign(keitaro_campaign_id=keitaro_campaign_id, name=remote.get("name", ""))
        db.add(camp)
        db.commit()
    return await sync_campaign(db, kc, camp)
