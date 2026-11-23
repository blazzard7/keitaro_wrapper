from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    geo: str = Field(pattern=r"^[A-Za-z]{2}$")
    offer_id: int
    domain_id: int
    group_id: int
    traffic_source_id: int


class ImportIn(BaseModel):
    keitaro_campaign_id: int


class OfferIn(BaseModel):
    offer_id: int


class PinIn(BaseModel):
    pinned: bool
    share: int | None = Field(default=None, ge=0, le=100)
