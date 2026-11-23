
import asyncio

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Campaign,
    Offer,
    Stream,
    StreamOffer,
    StreamOfferHistory,
)
from app.keitaro.client import KeitaroClient
from app.services.sync import sync_campaign


def make_sync_db(draft_share=50, published_share=50):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)

    campaign = Campaign(
        keitaro_campaign_id=123,
        name="Old campaign name",
    )
    db.add(campaign)
    db.flush()

    stream = Stream(
        campaign=campaign,
        keitaro_stream_id=101,
        name="Main stream",
        type="regular",
        schema="landings",
    )

    missing_stream = Stream(
        campaign=campaign,
        keitaro_stream_id=202,
        name="Missing stream",
        type="regular",
        schema="landings",
    )

    offer = Offer(
        keitaro_offer_id=1001,
        name="Old offer name",
    )

    db.add_all([stream, missing_stream, offer])
    db.flush()

    binding = StreamOffer(
        stream=stream,
        offer=offer,
        published_state="active",
        published_share=published_share,
        draft_state="active",
        draft_share=draft_share,
        is_pinned=False,
        keitaro_binding_id=501,
    )

    db.add(binding)
    db.commit()

    return engine, db, campaign, stream, missing_stream, binding


def make_client(remote_offers, remote_streams=None):
    if remote_streams is None:
        remote_streams = [
            {
                "id": 101,
                "name": "Main stream",
                "type": "regular",
                "schema": "landings",
                "position": 1,
                "state": "active",
                "offers": remote_offers,
            }
        ]

    async def handler(request):
        if request.url.path == "/campaigns/123":
            return httpx.Response(
                200,
                json={
                    "id": 123,
                    "name": "Updated campaign",
                    "alias": "updated-campaign",
                },
            )

        if request.url.path == "/campaigns/123/streams":
            return httpx.Response(200, json=remote_streams)

        if request.url.path == "/offers":
            return httpx.Response(
                200,
                json=[
                    {"id": 1001, "name": "Updated offer name"},
                ],
            )

        return httpx.Response(404, json={"error": "Unexpected request"})

    return KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=False,
        transport=httpx.MockTransport(handler),
    )


def test_sync_archives_offer_removed_in_keitaro():
    engine, db, campaign, stream, missing_stream, binding = make_sync_db()

    kc = make_client(remote_offers=[])

    asyncio.run(sync_campaign(db, kc, campaign))

    db.refresh(binding)
    db.refresh(stream)
    db.refresh(missing_stream)

    # Привязка не удалена физически.
    assert db.get(StreamOffer, binding.id) is not None

    # Оффер архивирован.
    assert binding.published_state == "removed"
    assert binding.published_share == 0
    assert binding.draft_state == "removed"
    assert binding.draft_share == 0

    # Поток, отсутствующий в Keitaro, помечен удалённым.
    assert missing_stream.remote_deleted is True

    # История содержит событие удаления.
    history = db.scalars(select(StreamOfferHistory)).all()
    assert len(history) == 1
    assert history[0].action == "removed_in_keitaro"

    # Кампания и справочник обновились.
    assert campaign.name == "Updated campaign"
    assert binding.offer.name == "Updated offer name"

    db.close()
    engine.dispose()


def test_sync_preserves_dirty_draft_when_offer_exists():
    engine, db, campaign, stream, missing_stream, binding = make_sync_db(
        draft_share=60,
        published_share=50,
    )

    kc = make_client(
        remote_offers=[
            {
                "id": 501,
                "offer_id": 1001,
                "share": 40,
                "state": "active",
            }
        ]
    )

    asyncio.run(sync_campaign(db, kc, campaign))

    db.refresh(binding)

    # Опубликованное состояние обновилось по данным Keitaro.
    assert binding.published_state == "active"
    assert binding.published_share == 40

    # Локальный черновик не затёрся.
    assert binding.draft_state == "active"
    assert binding.draft_share == 60

    # ID привязки тоже обновился.
    assert binding.keitaro_binding_id == 501

    db.close()
    engine.dispose()


def test_sync_is_idempotent_for_removed_offer():
    engine, db, campaign, stream, missing_stream, binding = make_sync_db()

    kc = make_client(remote_offers=[])

    asyncio.run(sync_campaign(db, kc, campaign))
    asyncio.run(sync_campaign(db, kc, campaign))

    db.refresh(binding)

    history = db.scalars(select(StreamOfferHistory)).all()

    # Повторная синхронизация не создаёт дубликаты истории.
    assert len(history) == 1
    assert history[0].action == "removed_in_keitaro"

    # Привязка всё ещё существует в единственном экземпляре.
    bindings = db.scalars(
        select(StreamOffer).where(StreamOffer.stream_id == stream.id)
    ).all()

    assert len(bindings) == 1
    assert bindings[0].published_state == "removed"

    db.close()
    engine.dispose()

# 2 --------------------------------------------------------
def test_sync_restores_offer_that_reappeared_in_keitaro():
    engine, db, campaign, stream, missing_stream, binding = make_sync_db()

    kc_removed = make_client(remote_offers=[])
    asyncio.run(sync_campaign(db, kc_removed, campaign))

    db.refresh(binding)
    assert binding.published_state == "removed"
    assert binding.draft_state == "removed"

    kc_restored = make_client(
        remote_offers=[
            {"id": 501, "offer_id": 1001, "share": 100, "state": "active"}
        ]
    )
    asyncio.run(sync_campaign(db, kc_restored, campaign))

    db.refresh(binding)

    assert binding.published_state == "active"
    assert binding.published_share == 100
    assert binding.draft_state == "active"
    assert binding.draft_share == 100
    assert binding.keitaro_binding_id == 501

    bindings = db.scalars(
        select(StreamOffer).where(StreamOffer.stream_id == stream.id)
    ).all()
    assert len(bindings) == 1

    history = db.scalars(select(StreamOfferHistory)).all()
    assert len(history) == 2
    assert {h.action for h in history} == {"removed_in_keitaro", "synced_update"}

    db.close()
    engine.dispose()

# 3 ---------
def test_sync_preserves_dirty_draft_when_offer_reappears():
    engine, db, campaign, stream, missing_stream, binding = make_sync_db()

    # Создаём локальное изменение: опубликовано 50%, в черновике 60%.
    binding.published_share = 50
    binding.draft_share = 60
    db.commit()

    # Оффер исчезает из Keitaro.
    kc_removed = make_client(remote_offers=[])
    asyncio.run(sync_campaign(db, kc_removed, campaign))

    db.refresh(binding)

    assert binding.published_state == "removed"
    assert binding.published_share == 0

    # Локальный черновик должен сохраниться.
    assert binding.draft_state == "active"
    assert binding.draft_share == 60

    # Оффер снова появляется в Keitaro с долей 40%.
    kc_restored = make_client(
        remote_offers=[
            {"id": 501, "offer_id": 1001, "share": 40, "state": "active"}
        ]
    )
    asyncio.run(sync_campaign(db, kc_restored, campaign))

    db.refresh(binding)

    # Опубликованное состояние обновилось из Keitaro.
    assert binding.published_state == "active"
    assert binding.published_share == 40

    # Локальный черновик не затёрся.
    assert binding.draft_state == "active"
    assert binding.draft_share == 60

    # Привязка восстановилась.
    assert binding.keitaro_binding_id == 501

    db.close()
    engine.dispose()

def test_sync_creates_new_stream_and_missing_offer():
    engine, db, campaign, stream, missing_stream, binding = make_sync_db()

    remote_streams = [
        {
            "id": 101,
            "name": "Main stream",
            "type": "regular",
            "schema": "landings",
            "offers": [],
        },
        {
            "id": 303,
            "name": "New stream",
            "type": "regular",
            "schema": "landings",
            "offers": [
                {
                    "id": 701,
                    "offer_id": 9999,
                    "share": 100,
                    "state": "active",
                }
            ],
        },
    ]

    kc = make_client([], remote_streams)

    asyncio.run(sync_campaign(db, kc, campaign))

    new_stream = db.scalar(
        select(Stream).where(Stream.keitaro_stream_id == 303)
    )
    assert new_stream is not None
    assert new_stream.name == "New stream"

    new_offer = db.scalar(
        select(Offer).where(Offer.keitaro_offer_id == 9999)
    )
    assert new_offer is not None
    assert new_offer.name == "Offer 9999"

    new_binding = db.scalar(
        select(StreamOffer).where(StreamOffer.stream_id == new_stream.id)
    )
    assert new_binding is not None
    assert new_binding.draft_share == 100
    assert new_binding.published_share == 100

    history = db.scalars(
        select(StreamOfferHistory).where(
            StreamOfferHistory.action == "synced_new"
        )
    ).all()
    assert len(history) == 1

    db.close()
    engine.dispose()