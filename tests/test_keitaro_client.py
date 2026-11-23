
import asyncio

import httpx
import pytest

from app.errors import KeitaroError, WriteDisabledError
from app.keitaro.client import KeitaroClient


def make_client(handler, write_enabled=False):
    return KeitaroClient(
        base_url="https://mock.keitaro",
        api_key="test-key",
        write_enabled=write_enabled,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("status_code", [403, 500])
def test_http_error_is_converted_to_keitaro_error(status_code):
    async def handler(request):
        return httpx.Response(status_code, text="API error")

    client = make_client(handler)

    with pytest.raises(KeitaroError, match=str(status_code)):
        asyncio.run(client.domains())


def test_network_error_is_converted_to_keitaro_error():
    async def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    client = make_client(handler)

    with pytest.raises(KeitaroError, match="Keitaro недоступен"):
        asyncio.run(client.domains())


def test_empty_response_returns_none():
    async def handler(request):
        return httpx.Response(204)

    client = make_client(handler)

    result = asyncio.run(
        client._request("GET", "/empty")
    )

    assert result is None


def test_groups_filters_non_campaign_groups():
    async def handler(request):
        return httpx.Response(
            200,
            json=[
                {"id": 1, "name": "Campaigns", "type": "campaigns"},
                {"id": 2, "name": "Offers", "type": "offers"},
                {"id": 3, "name": "Default group"},
            ],
        )

    client = make_client(handler)

    result = asyncio.run(client.groups())

    assert [group["id"] for group in result] == [1, 3]


def test_read_request_sends_api_key():
    received = []

    async def handler(request):
        received.append(request)
        return httpx.Response(200, json=[{"id": 1}])

    client = make_client(handler)

    result = asyncio.run(client.domains())

    assert result == [{"id": 1}]
    assert received[0].headers["Api-Key"] == "test-key"
    assert received[0].method == "GET"


def test_write_disabled_blocks_request():
    received = []

    async def handler(request):
        received.append(request)
        return httpx.Response(200, json={"id": 1})

    client = make_client(handler, write_enabled=False)

    with pytest.raises(WriteDisabledError):
        asyncio.run(client.create_campaign({"name": "Test"}))

    assert received == []
