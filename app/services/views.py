"""Сборка JSON для фронта из ORM-объектов."""
from app.models import Campaign, Stream, StreamOffer
from app.services import domain
from app.services.drafts import supports_offers


def binding_view(b: StreamOffer) -> dict:
    removed = b.draft_state == domain.REMOVED
    return {
        "offer_id": b.offer.keitaro_offer_id,
        "name": b.offer.name,
        "share": b.draft_share,
        "published_share": b.published_share,
        "state": b.draft_state,
        "archived": removed,
        "is_pinned": b.is_pinned,
        "dirty": domain.is_dirty(b),
        "in_keitaro": b.published_state == domain.ACTIVE,
    }


def stream_view(s: Stream) -> dict:
    offers = None  # у потока без офферов внутреннюю структуру не показываем
    if supports_offers(s):
        views = [binding_view(b) for b in s.bindings]
        offers = sorted(views, key=lambda v: (v["archived"], v["offer_id"]))
    return {
        "id": s.id,
        "keitaro_stream_id": s.keitaro_stream_id,
        "name": s.name,
        "type": s.type,
        "schema": s.schema,
        "position": s.position,
        "remote_deleted": s.remote_deleted,
        "dirty": domain.stream_is_dirty(s.bindings),
        "offers": offers,
    }


def campaign_view(c: Campaign) -> dict:
    return {
        "id": c.id,
        "keitaro_campaign_id": c.keitaro_campaign_id,
        "name": c.name,
        "alias": c.alias,
        "geo": c.geo,
        "last_synced_at": c.last_synced_at.isoformat() if c.last_synced_at else None,
        "streams": [stream_view(s) for s in c.streams],
    }
