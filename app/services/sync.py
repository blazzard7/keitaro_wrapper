"""
Синхронизация Keitaro -> локальная БД.

Принципы:
  * Ничего не удаляем физически: пропавший оффер -> published_state='removed' (архив),
    пропавший поток -> remote_deleted=True.
  * Неопубликованные черновики не затираются (см. domain.merge_remote_*).
  * Здесь только ЧТЕНИЕ из Keitaro.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.keitaro.client import KeitaroClient
from app.models import Campaign, Offer, Stream, StreamOffer, StreamOfferHistory
from app.services import domain


def log(db: Session, b: StreamOffer, action: str, before=None, after=None) -> None:
    db.add(StreamOfferHistory(
        stream_offer_id=b.id, stream_id=b.stream_id, keitaro_offer_id=b.offer.keitaro_offer_id,
        action=action, before=before, after=after,
    ))


def snapshot(b: StreamOffer) -> dict:
    return {"draft_state": b.draft_state, "draft_share": b.draft_share,
            "published_state": b.published_state, "published_share": b.published_share,
            "is_pinned": b.is_pinned}


async def refresh_offer_catalog(db: Session, kc: KeitaroClient) -> dict[int, Offer]:
    """Обновляет локальный справочник офферов; возвращает {keitaro_offer_id: Offer}."""
    local = {o.keitaro_offer_id: o for o in db.scalars(select(Offer))}
    for item in await kc.offers():
        o = local.get(item["id"])
        name = item.get("name") or f"Offer {item['id']}"
        if o is None:
            o = Offer(keitaro_offer_id=item["id"], name=name)
            db.add(o)
            local[item["id"]] = o
        else:
            o.name = name
    db.flush()
    return local


def _remote_offers(remote_stream: dict) -> dict[int, dict]:
    """{keitaro_offer_id: {share, id}} только по активным офферам потока."""
    res = {}
    for o in remote_stream.get("offers") or []:
        if o.get("state", "active") != "active":
            continue
        res[o["offer_id"]] = {"share": int(o.get("share") or 0), "id": o.get("id")}
    return res


async def sync_campaign(db: Session, kc: KeitaroClient, campaign: Campaign) -> Campaign:
    remote_c = await kc.get_campaign(campaign.keitaro_campaign_id)
    remote_streams = await kc.campaign_streams(campaign.keitaro_campaign_id)
    catalog = await refresh_offer_catalog(db, kc)

    campaign.name = remote_c.get("name", campaign.name)
    campaign.alias = remote_c.get("alias", campaign.alias)

    local_streams = {s.keitaro_stream_id: s for s in campaign.streams}
    seen = set()

    for rs in remote_streams:
        seen.add(rs["id"])
        s = local_streams.get(rs["id"])
        if s is None:
            s = Stream(campaign_id=campaign.id, keitaro_stream_id=rs["id"], type=rs.get("type", "regular"))
            db.add(s)
            campaign.streams.append(s)
        s.name = rs.get("name") or ""
        s.type = rs.get("type", s.type)
        s.schema = rs.get("schema")
        s.position = rs.get("position") or 0
        s.state = rs.get("state")
        s.remote_deleted = False
        db.flush()

        remote_offers = _remote_offers(rs)
        by_offer = {b.offer.keitaro_offer_id: b for b in s.bindings}

        for k_offer_id, info in remote_offers.items():
            offer = catalog.get(k_offer_id)
            if offer is None:  # оффер есть в потоке, но не в /offers - заводим заглушку
                offer = Offer(keitaro_offer_id=k_offer_id, name=f"Offer {k_offer_id}")
                db.add(offer)
                db.flush()
                catalog[k_offer_id] = offer
            b = by_offer.get(k_offer_id)
            if b is None:
                b = StreamOffer(stream_id=s.id, offer_id=offer.id, offer=offer)
                s.bindings.append(b)
                domain.merge_remote_present(b, info["share"], info["id"])
                b.draft_state, b.draft_share = domain.ACTIVE, info["share"]
                db.flush()
                log(db, b, "synced_new", after=snapshot(b))
            else:
                before = snapshot(b)
                domain.merge_remote_present(b, info["share"], info["id"])
                if snapshot(b) != before:
                    log(db, b, "synced_update", before, snapshot(b))

        # Оффер удалили прямо в Keitaro: привязку сохраняем, помечаем removed.
        for k_offer_id, b in by_offer.items():
            if k_offer_id not in remote_offers:
                before = snapshot(b)
                if domain.merge_remote_absent(b):
                    log(db, b, "removed_in_keitaro", before, snapshot(b))

    for k_stream_id, s in local_streams.items():
        if k_stream_id is not None and k_stream_id not in seen:
            s.remote_deleted = True

    campaign.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return campaign


def get_campaign_or_404(db: Session, campaign_id: int) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None:
        raise NotFoundError(f"Кампания {campaign_id} не найдена")
    return c
