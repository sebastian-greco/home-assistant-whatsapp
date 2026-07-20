"""Tests for the provider-independent WAHA API client."""

from typing import Any

import pytest

from ._loader import load_integration_module

api = load_integration_module("api")


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, data: Any) -> None:
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self, *, content_type=None):
        return self._data

    async def text(self) -> str:
        return str(self._data)


class FakeSession:
    """Capture requests and return queued responses."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def make_client(session: FakeSession):
    """Create a client with stable test connection values."""
    return api.WahaClient(
        session,
        "secret-api-key",
        "house",
        base_url="http://waha.internal:3000/",
    )


@pytest.mark.asyncio
async def test_get_server() -> None:
    """The account check reads WAHA version metadata with its API key."""
    session = FakeSession(
        FakeResponse(
            200,
            {"version": "2026.7.1", "engine": "GOWS", "tier": "PLUS"},
        )
    )
    client = make_client(session)

    server = await client.async_get_server()

    assert server.version == "2026.7.1"
    assert server.engine == "GOWS"
    assert session.requests[0]["url"] == "http://waha.internal:3000/api/version"
    assert session.requests[0]["headers"]["X-Api-Key"] == "secret-api-key"


@pytest.mark.asyncio
async def test_get_configured_session() -> None:
    """The configured session is resolved from the WAHA session list."""
    session = FakeSession(
        FakeResponse(
            200,
            [
                {"name": "other", "status": "STOPPED"},
                {
                    "name": "house",
                    "status": "WORKING",
                    "me": {"id": "393330000000@c.us", "pushName": "Home"},
                },
            ],
        )
    )
    client = make_client(session)

    result = await client.async_get_session()

    assert result.name == "house"
    assert result.status == "WORKING"
    assert result.push_name == "Home"
    assert session.requests[0]["params"] == {"all": "true"}


@pytest.mark.asyncio
async def test_missing_session_has_dedicated_error() -> None:
    """A typo in the session name produces a useful config-flow error."""
    client = make_client(FakeSession(FakeResponse(200, [])))

    with pytest.raises(api.WahaSessionNotFoundError, match="house"):
        await client.async_get_session()


@pytest.mark.asyncio
async def test_send_text_payload() -> None:
    """Free-form text uses WAHA's documented sendText payload."""
    session = FakeSession(FakeResponse(200, {"key": {"id": "ABCD1234"}}))
    client = make_client(session)

    result = await client.async_send_text(
        "+39 333 123 4567", "Door open", link_preview=False
    )

    assert result.id == "ABCD1234"
    assert result.chat_id == "393331234567@c.us"
    assert session.requests[0]["url"].endswith("/api/sendText")
    assert session.requests[0]["json"] == {
        "session": "house",
        "chatId": "393331234567@c.us",
        "text": "Door open",
        "linkPreview": False,
    }


@pytest.mark.asyncio
async def test_gows_nested_message_id_is_supported() -> None:
    """GOWS engine variants may nest the WhatsApp message ID."""
    session = FakeSession(
        FakeResponse(200, {"_data": {"id": {"id": "GOWS-MESSAGE-ID"}}})
    )
    result = await make_client(session).async_send_text("393331234567", "Hello")

    assert result.id == "GOWS-MESSAGE-ID"


@pytest.mark.asyncio
async def test_authentication_error() -> None:
    """Authentication failures use a dedicated exception for HA reauth."""
    session = FakeSession(FakeResponse(401, {"message": "Bad API key"}))
    client = make_client(session)

    with pytest.raises(api.WahaAuthenticationError, match="Bad API key"):
        await client.async_get_server()
