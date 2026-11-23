from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.keitaro.client import KeitaroClient, get_keitaro
from app.models import Campaign
from app.schemas import CampaignCreate, ImportIn
from app.services import campaigns as svc
from app.services.publish import publish_campaign
from app.services.sync import get_campaign_or_404, sync_campaign
from app.services.views import campaign_view

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("")
def list_campaigns(db: Session = Depends(get_db)):
    rows = db.scalars(select(Campaign).order_by(Campaign.id.desc()))
    return [{"id": c.id, "keitaro_campaign_id": c.keitaro_campaign_id, "name": c.name, "geo": c.geo}
            for c in rows]


@router.post("")
async def create(data: CampaignCreate, db: Session = Depends(get_db),
                 kc: KeitaroClient = Depends(get_keitaro)):
    c = await svc.create_campaign(db, kc, **data.model_dump())
    return campaign_view(c)


@router.post("/import")
async def import_(data: ImportIn, db: Session = Depends(get_db),
                  kc: KeitaroClient = Depends(get_keitaro)):
    return campaign_view(await svc.import_campaign(db, kc, data.keitaro_campaign_id))


@router.get("/{campaign_id}")
async def open_campaign(campaign_id: int, refresh: bool = True, db: Session = Depends(get_db),
                        kc: KeitaroClient = Depends(get_keitaro)):
    """refresh=true (по умолчанию): при открытии подтягиваем актуальные потоки из Keitaro."""
    c = get_campaign_or_404(db, campaign_id)
    if refresh:
        await sync_campaign(db, kc, c)
    return campaign_view(c)


@router.post("/{campaign_id}/publish")
async def publish(campaign_id: int, db: Session = Depends(get_db),
                  kc: KeitaroClient = Depends(get_keitaro)):
    c = get_campaign_or_404(db, campaign_id)
    result = await publish_campaign(db, kc, c)
    return {**result, "campaign": campaign_view(c)}
