
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.errors import KeitaroError
from app.db import get_db
from app.main import app
from app.keitaro.client import get_keitaro
from app.models import Base


class FakeKeitaro:
    def __init__(self):
        self.streams = []
        self.created_campaigns = []
        self.next_stream_id = 100
        self.create_stream_calls = 0
        self.fail_create_stream_at = None
        self.fail_get_campaign = False

    async def offers(self):
        return [{"id": 10, "name": "Test offer"}]

    async def domains(self):
        return [{"id": 20, "name": "Test domain"}]

    async def groups(self):
        return [{"id": 30, "name": "Test group", "type": "campaigns"}]

    async def traffic_sources(self):
        return [{"id": 40, "name": "Test source"}]

    async def create_campaign(self, payload):
        self.created_campaigns.append(payload)
        return {"id": 900, "name": payload["name"], "alias": "test-alias"}

    async def campaign_streams(self, campaign_id):
        return self.streams

    async def create_stream(self, payload):
        self.create_stream_calls += 1
        if self.create_stream_calls == self.fail_create_stream_at:
            raise KeitaroError("Test stream creation failure")
        self.next_stream_id += 1
        stream = {
            **payload,
            "id": self.next_stream_id,
            "offers": [
                {
                    "id": self.next_stream_id + 1000,
                    **offer,
                }
                for offer in payload.get("offers", [])
            ],
        }
        self.streams.append(stream)
        return stream

    async def update_stream(self, stream_id, payload):
        return {"id": stream_id, **payload}

    async def get_campaign(self, campaign_id):
        if self.fail_get_campaign:
            raise KeitaroError("Test sync failure")

        return {
        "id": campaign_id,
        "name": "Imported campaign",
        "alias": "imported",
    }


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    fake = FakeKeitaro()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_keitaro] = lambda: fake

    with TestClient(app) as client:
        yield client, fake

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_create_campaign_success(api):
    client, fake = api

    response = client.post(
        "/api/campaigns",
        json={
            "name": "My test campaign",
            "geo": "au",
            "offer_id": 10,
            "domain_id": 20,
            "group_id": 30,
            "traffic_source_id": 40,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["keitaro_campaign_id"] == 900
    assert data["geo"] == "AU"
    assert len(data["streams"]) == 2

    geo = next(s for s in data["streams"] if s["name"] == "GEO AU")
    default = next(s for s in data["streams"] if s["type"] == "default")

    assert geo["schema"] == "action"
    assert default["offers"][0]["offer_id"] == 10
    assert default["offers"][0]["share"] == 100
    assert len(fake.created_campaigns) == 1


def test_create_campaign_invalid_geo(api):
    client, fake = api

    response = client.post(
        "/api/campaigns",
        json={
            "name": "Invalid GEO",
            "geo": "Australia",
            "offer_id": 10,
            "domain_id": 20,
            "group_id": 30,
            "traffic_source_id": 40,
        },
    )

    assert response.status_code == 422
    assert len(fake.created_campaigns) == 0


def test_create_campaign_missing_offer(api):
    client, fake = api

    response = client.post(
        "/api/campaigns",
        json={
            "name": "Missing offer",
            "geo": "AU",
            "offer_id": 999,
            "domain_id": 20,
            "group_id": 30,
            "traffic_source_id": 40,
        },
    )

    assert response.status_code == 400
    assert len(fake.created_campaigns) == 0


def test_import_campaign_success(api):
    client, fake = api

    response = client.post(
        "/api/campaigns/import",
        json={"keitaro_campaign_id": 777},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["keitaro_campaign_id"] == 777
    assert data["name"] == "Imported campaign"
    assert data["streams"] == []


def test_create_campaign_geo_stream_failure_preserves_campaign(api):
    client, fake = api
    fake.fail_create_stream_at = 1

    response = client.post(
        "/api/campaigns",
        json={
            "name": "Partial GEO failure",
            "geo": "AU",
            "offer_id": 10,
            "domain_id": 20,
            "group_id": 30,
            "traffic_source_id": 40,
        },
    )

    assert response.status_code == 502
    assert "Кампания #900 создана" in response.json()["detail"]
    assert len(fake.created_campaigns) == 1
    assert len(fake.streams) == 0

    campaigns = client.get("/api/campaigns").json()
    assert len(campaigns) == 1
    assert campaigns[0]["keitaro_campaign_id"] == 900


def test_create_campaign_default_stream_failure_preserves_geo(api):
    client, fake = api
    fake.fail_create_stream_at = 2

    response = client.post(
        "/api/campaigns",
        json={
            "name": "Partial default failure",
            "geo": "AU",
            "offer_id": 10,
            "domain_id": 20,
            "group_id": 30,
            "traffic_source_id": 40,
        },
    )

    assert response.status_code == 502
    assert "Кампания #900 создана" in response.json()["detail"]
    assert len(fake.created_campaigns) == 1
    assert len(fake.streams) == 1
    assert fake.streams[0]["name"] == "GEO AU"

    campaigns = client.get("/api/campaigns").json()
    assert len(campaigns) == 1
    assert campaigns[0]["keitaro_campaign_id"] == 900


def test_create_campaign_sync_failure_can_be_recovered(api):
    client, fake = api
    fake.fail_get_campaign = True

    response = client.post(
        "/api/campaigns",
        json={
            "name": "Sync failure",
            "geo": "AU",
            "offer_id": 10,
            "domain_id": 20,
            "group_id": 30,
            "traffic_source_id": 40,
        },
    )

    assert response.status_code == 502
    assert len(fake.streams) == 2

    campaigns = client.get("/api/campaigns").json()
    assert len(campaigns) == 1

    fake.fail_get_campaign = False

    recovered = client.get("/api/campaigns/1?refresh=true")

    assert recovered.status_code == 200
    assert len(recovered.json()["streams"]) == 2
