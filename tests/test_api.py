
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.keitaro.client import get_keitaro
from app.db import get_db
from app.main import app
from app.models import Base, Campaign, Offer, Stream, StreamOffer


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)

    campaign = Campaign(
        keitaro_campaign_id=123,
        name="API test campaign",
    )
    db.add(campaign)
    db.flush()

    stream = Stream(
        campaign=campaign,
        keitaro_stream_id=101,
        name="API test stream",
        type="regular",
        schema="landings",
    )

    offer1 = Offer(keitaro_offer_id=1001, name="Offer 1")
    offer2 = Offer(keitaro_offer_id=1002, name="Offer 2")
    offer3 = Offer(keitaro_offer_id=1003, name="Offer 3")

    db.add_all([stream, offer1, offer2, offer3])
    db.flush()

    db.add_all([
        StreamOffer(
            stream=stream,
            offer=offer1,
            published_state="active",
            published_share=50,
            draft_state="active",
            draft_share=50,
            is_pinned=False,
            keitaro_binding_id=501,
        ),
        StreamOffer(
            stream=stream,
            offer=offer2,
            published_state="active",
            published_share=50,
            draft_state="active",
            draft_share=50,
            is_pinned=False,
            keitaro_binding_id=502,
        ),
    ])
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, stream.id

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_add_offer_endpoint(api):
    client, stream_id = api

    response = client.post(
        f"/api/streams/{stream_id}/offers",
        json={"offer_id": 1003},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["id"] == stream_id

    offers = data["offers"]
    assert len(offers) == 3
    assert sum(o["share"] for o in offers if not o["archived"]) == 100
    assert any(o["offer_id"] == 1003 for o in offers)


def test_remove_offer_endpoint(api):
    client, stream_id = api

    response = client.delete(
        f"/api/streams/{stream_id}/offers/1001"
    )

    assert response.status_code == 200

    offers = response.json()["offers"]
    removed = next(o for o in offers if o["offer_id"] == 1001)

    assert removed["archived"] is True
    assert removed["share"] == 0


def test_restore_offer_endpoint(api):
    client, stream_id = api

    client.delete(f"/api/streams/{stream_id}/offers/1001")

    response = client.post(
        f"/api/streams/{stream_id}/offers/1001/restore"
    )

    assert response.status_code == 200

    offers = response.json()["offers"]
    restored = next(o for o in offers if o["offer_id"] == 1001)

    assert restored["archived"] is False
    assert sum(o["share"] for o in offers if not o["archived"]) == 100


def test_pin_offer_endpoint(api):
    client, stream_id = api

    response = client.put(
        f"/api/streams/{stream_id}/offers/1001/pin",
        json={"pinned": True, "share": 60},
    )

    assert response.status_code == 200

    offers = response.json()["offers"]
    pinned = next(o for o in offers if o["offer_id"] == 1001)
    other = next(o for o in offers if o["offer_id"] == 1002)

    assert pinned["is_pinned"] is True
    assert pinned["share"] == 60
    assert other["share"] == 40


def test_cancel_endpoint(api):
    client, stream_id = api

    client.delete(f"/api/streams/{stream_id}/offers/1001")

    response = client.post(f"/api/streams/{stream_id}/cancel")

    assert response.status_code == 200

    offers = response.json()["offers"]

    assert all(not o["archived"] for o in offers)
    assert sum(o["share"] for o in offers) == 100


def test_history_endpoint(api):
    client, stream_id = api

    client.delete(f"/api/streams/{stream_id}/offers/1001")

    response = client.get(f"/api/streams/{stream_id}/history")

    assert response.status_code == 200

    history = response.json()

    assert len(history) == 1
    assert history[0]["offer_id"] == 1001
    assert history[0]["action"] == "remove"


def test_stream_not_found(api):
    client, _ = api

    response = client.post("/api/streams/999999/cancel")

    assert response.status_code == 404


def test_invalid_pin_payload(api):
    client, stream_id = api

    response = client.put(
        f"/api/streams/{stream_id}/offers/1001/pin",
        json={"pinned": True, "share": 150},
    )

    assert response.status_code == 422


def test_publish_stream_endpoint(api):
    client, stream_id = api

    class FakeKeitaro:
        async def update_stream(self, keitaro_stream_id, payload):
            assert keitaro_stream_id == 101
            assert len(payload["offers"]) == 2

            return {
                "offers": [
                    {"id": 901, "offer_id": 1001},
                    {"id": 902, "offer_id": 1002},
                ]
            }

    app.dependency_overrides[get_keitaro] = lambda: FakeKeitaro()

    try:
        # Меняем долю, чтобы поток стал dirty.
        pin_response = client.put(
            f"/api/streams/{stream_id}/offers/1001/pin",
            json={"pinned": True, "share": 60},
        )
        assert pin_response.status_code == 200

        response = client.post(
            f"/api/streams/{stream_id}/publish"
        )

        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "published"
        assert data["stream"]["offers"]

        published = {
            offer["offer_id"]: offer
            for offer in data["stream"]["offers"]
        }

        assert published[1001]["published_share"] == 60
        assert published[1002]["published_share"] == 40

    finally:
        app.dependency_overrides.pop(get_keitaro, None)

def test_rebalance_endpoint(api):
    client, stream_id = api

    response = client.post(
        f"/api/streams/{stream_id}/rebalance"
    )

    assert response.status_code == 200

    offers = response.json()["offers"]
    assert sum(o["share"] for o in offers if not o["archived"]) == 100