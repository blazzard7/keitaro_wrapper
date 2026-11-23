
import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import db
from app.errors import WriteDisabledError
from app.keitaro.client import KeitaroClient
from app.models import Base, Campaign, Offer, Stream, StreamOffer, StreamOfferHistory
from app.services.publish import publish_stream, publish_campaign


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)

    campaign = Campaign(
        keitaro_campaign_id=999,
        name="Test campaign",
    )
    db.add(campaign)
    db.flush()

    stream = Stream(
        campaign=campaign,
        keitaro_stream_id=101,
        name="Test stream",
        type="regular",
        schema="landings",
    )
    db.add(stream)
    db.flush()

    offer1 = Offer(keitaro_offer_id=1001, name="Offer 1")
    offer2 = Offer(keitaro_offer_id=1002, name="Offer 2")
    db.add_all([offer1, offer2])
    db.flush()

    binding1 = StreamOffer(
        stream=stream,
        offer=offer1,
        published_state="active",
        published_share=50,
        draft_state="active",
        draft_share=60,
    )
    binding2 = StreamOffer(
        stream=stream,
        offer=offer2,
        published_state="active",
        published_share=50,
        draft_state="active",
        draft_share=40,
    )

    db.add_all([binding1, binding2])
    db.commit()

    return engine, db, stream, binding1, binding2


def test_publish_success():
    engine, db, stream, b1, b2 = make_db()
    received_requests = []

    async def handler(request):
        received_requests.append(request)

        return httpx.Response(
            200,
            json={
                "offers": [
                    {"id": 501, "offer_id": 1001},
                    {"id": 502, "offer_id": 1002},
                ]
            },
        )

    kc = KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=True,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(publish_stream(db, kc, stream))

    assert result["status"] == "published"
    assert len(received_requests) == 1

    request = received_requests[0]
    assert request.method == "PUT"
    assert request.url.path == "/streams/101"
    assert request.headers["Api-Key"] == "test-key"

    payload = request.read().decode()
    assert '"offer_id":1001' in payload
    assert '"offer_id":1002' in payload

    db.refresh(b1)
    db.refresh(b2)

    assert b1.published_share == 60
    assert b2.published_share == 40
    assert b1.keitaro_binding_id == 501
    assert b2.keitaro_binding_id == 502

    history = db.scalars(select(StreamOfferHistory)).all()
    assert len(history) == 2

    db.close()
    engine.dispose()


def test_publish_keitaro_error_preserves_draft():
    engine, db, stream, b1, b2 = make_db()
    received_requests = []

    async def handler(request):
        received_requests.append(request)
        return httpx.Response(500, text="Internal Server Error")

    kc = KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=True,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(publish_stream(db, kc, stream))

    assert result["status"] == "error"
    assert "500" in result["error"]
    assert len(received_requests) == 1

    db.refresh(b1)
    db.refresh(b2)

    # Черновик остался прежним.
    assert b1.draft_share == 60
    assert b2.draft_share == 40

    # Опубликованное состояние не изменилось.
    assert b1.published_share == 50
    assert b2.published_share == 50

    # История публикации не появилась.
    history = db.scalars(select(StreamOfferHistory)).all()
    assert len(history) == 0

    db.close()
    engine.dispose()


def test_publish_disabled_does_not_send_request():
    engine, db, stream, b1, b2 = make_db()
    received_requests = []

    async def handler(request):
        received_requests.append(request)
        return httpx.Response(200, json={})

    kc = KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=False,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(WriteDisabledError):
        asyncio.run(publish_stream(db, kc, stream))

    assert len(received_requests) == 0

    db.refresh(b1)
    db.refresh(b2)

    assert b1.published_share == 50
    assert b2.published_share == 50

    db.close()
    engine.dispose()

# 2 --------------------------------------------------------
def test_publish_archived_offer():
    engine, db, stream, b1, b2 = make_db()
    received_payloads = []

    # Первый оффер остаётся, второй архивируем.
    b1.draft_share = 100
    b2.draft_state = "removed"
    b2.draft_share = 0
    db.commit()

    async def handler(request):
        import json

        payload = json.loads(request.content)
        received_payloads.append(payload)

        return httpx.Response(
            200,
            json={
                "offers": [
                    {"id": 501, "offer_id": 1001},
                ]
            },
        )

    kc = KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=True,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(publish_stream(db, kc, stream))

    assert result["status"] == "published"

    # В Keitaro отправлен только активный оффер.
    assert len(received_payloads) == 1
    assert len(received_payloads[0]["offers"]) == 1
    assert received_payloads[0]["offers"][0]["offer_id"] == 1001

    db.refresh(b1)
    db.refresh(b2)

    # Первый оффер опубликован с долей 100%.
    assert b1.published_state == "active"
    assert b1.published_share == 100

    # Второй сохранён локально, но переведён в архив.
    assert b2.published_state == "removed"
    assert b2.published_share == 0
    assert db.get(StreamOffer, b2.id) is not None

    history = db.scalars(select(StreamOfferHistory)).all()
    assert len(history) == 2

    db.close()
    engine.dispose()

def test_publish_remote_deleted_stream():
    engine, db, stream, b1, b2 = make_db()
    stream.remote_deleted = True
    db.commit()

    result = asyncio.run(publish_campaign(db, None, stream.campaign))

    assert result["status"] == "error"
    assert result["streams"][0]["status"] == "error"
    assert "удалён" in result["streams"][0]["error"]

    db.close()
    engine.dispose()


def test_publish_unchanged_does_not_send_request():
    engine, db, stream, b1, b2 = make_db()
    b1.draft_share = 50
    b2.draft_share = 50
    db.commit()
    received_requests = []

    async def handler(request):
        received_requests.append(request)
        return httpx.Response(200, json={})

    kc = KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=True,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(publish_stream(db, kc, stream))

    assert result["status"] == "unchanged"
    assert len(received_requests) == 0

    db.close()
    engine.dispose()


def test_publish_invalid_shares_preserves_draft():
    engine, db, stream, b1, b2 = make_db()
    b1.draft_share = 70
    b2.draft_share = 40
    db.commit()

    received_requests = []

    async def handler(request):
        received_requests.append(request)
        return httpx.Response(200, json={})

    kc = KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=True,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(publish_stream(db, kc, stream))

    assert result["status"] == "error"
    assert "110%" in result["error"]
    assert len(received_requests) == 0

    db.refresh(b1)
    db.refresh(b2)

    assert b1.draft_share == 70
    assert b2.draft_share == 40
    assert b1.published_share == 50
    assert b2.published_share == 50

    db.close()
    engine.dispose()
