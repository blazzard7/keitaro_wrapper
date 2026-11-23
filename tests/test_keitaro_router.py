
from fastapi.testclient import TestClient

from app.main import app
from app.keitaro.client import get_keitaro


class FakeKeitaro:
    async def domains(self):
        return [{"id": 1, "name": "Domain 1", "extra": "ignored"}]

    async def groups(self):
        return [{"id": 2, "name": "Group 1", "extra": "ignored"}]

    async def traffic_sources(self):
        return [{"id": 3, "name": "Source 1", "extra": "ignored"}]

    async def offers(self):
        return [
            {"id": 10, "name": "Alpha Offer"},
            {"id": 20, "name": "Beta Offer"},
            {"id": 30, "name": "Gamma"},
        ]


def test_keitaro_reference_endpoints():
    fake = FakeKeitaro()
    app.dependency_overrides[get_keitaro] = lambda: fake

    try:
        with TestClient(app) as client:
            assert client.get("/api/keitaro/domains").json() == [
                {"id": 1, "name": "Domain 1"}
            ]

            assert client.get("/api/keitaro/groups").json() == [
                {"id": 2, "name": "Group 1"}
            ]

            assert client.get("/api/keitaro/traffic-sources").json() == [
                {"id": 3, "name": "Source 1"}
            ]

            assert len(client.get("/api/keitaro/offers").json()) == 3

            assert client.get("/api/keitaro/offers?q=alpha").json() == [
                {"id": 10, "name": "Alpha Offer"}
            ]

            assert client.get("/api/keitaro/offers?q=20").json() == [
                {"id": 20, "name": "Beta Offer"}
            ]

            assert len(client.get("/api/keitaro/offers?limit=1").json()) == 1

    finally:
        app.dependency_overrides.pop(get_keitaro, None)
