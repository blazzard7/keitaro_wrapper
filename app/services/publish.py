"""
Публикация (Push to Keitaro).

Keitaro не умеет транзакции на несколько запросов, поэтому:
  * один поток = одна атомарная единица (один PUT /streams/{id});
  * published_* и сброс dirty записываются в БД ТОЛЬКО после успешного ответа Keitaro;
  * при ошибке потока его локальное состояние не меняется (остаётся dirty), остальные потоки
    всё равно публикуются, а результат возвращается по каждому потоку отдельно;
  * если Keitaro принял PUT, а коммит в БД упал - следующий Sync увидит реальное состояние
    Keitaro и приведёт published_* в порядок (черновик при этом не теряется).
"""
from sqlalchemy.orm import Session

from app.errors import DomainError, KeitaroError, WriteDisabledError
from app.keitaro.client import KeitaroClient
from app.models import Campaign, Stream
from app.services import domain
from app.services.sync import log, snapshot


def build_offers_payload(s: Stream) -> list[dict]:
    """В Keitaro уходят только активные офферы. Удалённые из списка пропадают (и остаются в нашем архиве)."""
    return [
        {"offer_id": b.offer.keitaro_offer_id, "share": b.draft_share, "state": "active"}
        for b in s.bindings if b.draft_state == domain.ACTIVE
    ]


async def publish_stream(db: Session, kc: KeitaroClient, s: Stream) -> dict:
    res = {"stream_id": s.id, "name": s.name}
    if s.remote_deleted:
        return {**res, "status": "error", "error": "Поток удалён в Keitaro"}
    if not domain.stream_is_dirty(s.bindings):
        return {**res, "status": "unchanged"}

    try:
        domain.validate_for_publish(s.bindings)
        remote = await kc.update_stream(s.keitaro_stream_id, {"offers": build_offers_payload(s)})
    except (DomainError, KeitaroError) as e:
        return {**res, "status": "error", "error": str(e)}  # локально ничего не менялось
    except WriteDisabledError:
        raise

    remote_ids = {}
    if isinstance(remote, dict):
        remote_ids = {o.get("offer_id"): o.get("id") for o in remote.get("offers") or []}

    try:
        for b in s.bindings:
            if domain.is_dirty(b):
                before = snapshot(b)
                domain.apply_published(b, remote_ids.get(b.offer.keitaro_offer_id))
                log(db, b, "published", before, snapshot(b))
        db.commit()
    except Exception as e:
        db.rollback()
        return {**res, "status": "error",
                "error": f"Keitaro обновлён, но локальная запись не сохранилась ({e}). Сделайте Sync."}
    return {**res, "status": "published"}


async def publish_campaign(db: Session, kc: KeitaroClient, campaign: Campaign) -> dict:
    results = [await publish_stream(db, kc, s) for s in campaign.streams]
    ok = [r for r in results if r["status"] == "published"]
    bad = [r for r in results if r["status"] == "error"]
    overall = "error" if bad and not ok else "partial" if bad else "published" if ok else "unchanged"
    return {"status": overall, "streams": results}
