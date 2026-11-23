from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.keitaro.client import KeitaroClient, get_keitaro
from app.models import StreamOfferHistory
from app.schemas import OfferIn, PinIn
from app.services import drafts
from app.services.publish import publish_stream
from app.services.views import stream_view

router = APIRouter(prefix="/api/streams", tags=["streams"])


@router.post("/{stream_id}/offers")
def add_offer(stream_id: int, data: OfferIn, db: Session = Depends(get_db)):
    return stream_view(drafts.add_offer(db, stream_id, data.offer_id))


@router.delete("/{stream_id}/offers/{offer_id}")
def remove_offer(stream_id: int, offer_id: int, db: Session = Depends(get_db)):
    return stream_view(drafts.remove_offer(db, stream_id, offer_id))


@router.post("/{stream_id}/offers/{offer_id}/restore")
def restore_offer(stream_id: int, offer_id: int, db: Session = Depends(get_db)):
    return stream_view(drafts.restore_offer(db, stream_id, offer_id))


@router.put("/{stream_id}/offers/{offer_id}/pin")
def pin_offer(stream_id: int, offer_id: int, data: PinIn, db: Session = Depends(get_db)):
    return stream_view(drafts.set_pin(db, stream_id, offer_id, data.pinned, data.share))


@router.post("/{stream_id}/rebalance")
def rebalance(stream_id: int, db: Session = Depends(get_db)):
    return stream_view(drafts.rebalance(db, stream_id))


@router.post("/{stream_id}/cancel")
def cancel(stream_id: int, db: Session = Depends(get_db)):
    return stream_view(drafts.cancel(db, stream_id))


@router.post("/{stream_id}/publish")
async def publish(stream_id: int, db: Session = Depends(get_db),
                  kc: KeitaroClient = Depends(get_keitaro)):
    s = drafts.get_stream(db, stream_id)
    result = await publish_stream(db, kc, s)
    return {**result, "stream": stream_view(s)}


@router.get("/{stream_id}/history")
def history(stream_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(StreamOfferHistory).where(StreamOfferHistory.stream_id == stream_id)
                      .order_by(StreamOfferHistory.id.desc()).limit(200))
    return [{"offer_id": r.keitaro_offer_id, "action": r.action, "before": r.before,
             "after": r.after, "at": r.created_at.isoformat() if r.created_at else None} for r in rows]
