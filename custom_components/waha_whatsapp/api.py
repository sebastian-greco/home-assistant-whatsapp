"""Async client for the WAHA HTTP API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession

from .helpers import normalize_chat_id

REQUEST_TIMEOUT_SECONDS = 15


class WahaError(Exception):
    """Base exception for WAHA API failures."""


class WahaConnectionError(WahaError):
    """Raised when WAHA cannot be reached."""


class WahaAuthenticationError(WahaError):
    """Raised when WAHA rejects the API key."""


class WahaRequestError(WahaError):
    """Raised when WAHA rejects a request."""

    def __init__(self, message: str, status: int) -> None:
        """Initialize the request error."""
        super().__init__(message)
        self.status = status


class WahaResponseError(WahaError):
    """Raised when WAHA returns an unexpected response."""


class WahaSessionNotFoundError(WahaError):
    """Raised when the configured WAHA session does not exist."""


@dataclass(frozen=True, slots=True)
class WahaServer:
    """Details reported by a WAHA server."""

    version: str
    engine: str | None
    tier: str | None


@dataclass(frozen=True, slots=True)
class WahaSession:
    """Current state of a configured WAHA session."""

    name: str
    status: str
    account_id: str | None
    push_name: str | None


@dataclass(frozen=True, slots=True)
class WahaMessage:
    """Result returned after WAHA accepts a message."""

    id: str | None
    chat_id: str


class WahaClient:
    """Small async client for a local WAHA server."""

    def __init__(
        self,
        session: ClientSession,
        api_key: str,
        session_name: str,
        *,
        base_url: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._api_key = api_key
        self.session_name = session_name
        self.base_url = base_url.rstrip("/")

    async def async_get_server(self) -> WahaServer:
        """Validate credentials and fetch WAHA version information."""
        data = await self._request("GET", "/api/version")
        if not isinstance(data, dict):
            raise WahaResponseError("WAHA returned invalid version information")

        version = data.get("version")
        if not isinstance(version, str) or not version:
            raise WahaResponseError("WAHA did not return its version")
        return WahaServer(
            version=version,
            engine=_optional_string(data.get("engine")),
            tier=_optional_string(data.get("tier")),
        )

    async def async_get_session(self) -> WahaSession:
        """Fetch the configured WAHA session from the server."""
        data = await self._request("GET", "/api/sessions", params={"all": "true"})
        if not isinstance(data, list):
            raise WahaResponseError("WAHA returned an invalid session list")

        for item in data:
            if isinstance(item, dict) and item.get("name") == self.session_name:
                status = item.get("status")
                if not isinstance(status, str):
                    raise WahaResponseError("WAHA returned an invalid session status")
                me = item.get("me") if isinstance(item.get("me"), dict) else {}
                return WahaSession(
                    name=self.session_name,
                    status=status,
                    account_id=_optional_string(me.get("id")),
                    push_name=_optional_string(me.get("pushName")),
                )

        raise WahaSessionNotFoundError(
            f"WAHA session '{self.session_name}' does not exist"
        )

    async def async_send_text(
        self,
        recipient: str,
        text: str,
        *,
        link_preview: bool = True,
    ) -> WahaMessage:
        """Send a free-form WhatsApp text message."""
        chat_id = normalize_chat_id(recipient)
        payload = {
            "session": self.session_name,
            "chatId": chat_id,
            "text": text,
            "linkPreview": link_preview,
        }
        data = await self._request("POST", "/api/sendText", json=payload)
        return WahaMessage(id=_message_id(data), chat_id=chat_id)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Perform one authenticated WAHA request."""
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._session.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    headers=headers,
                    json=json,
                    params=params,
                ) as response:
                    try:
                        data = await response.json(content_type=None)
                    except (TypeError, ValueError):
                        data = {"message": await response.text()}
                    status = response.status
        except TimeoutError as err:
            raise WahaConnectionError("The request to WAHA timed out") from err
        except (ClientError, OSError) as err:
            raise WahaConnectionError(f"Unable to connect to WAHA: {err}") from err

        if status < 400:
            return data

        message = _error_message(data, status)
        if status in (401, 403):
            raise WahaAuthenticationError(message)
        raise WahaRequestError(message, status)


def _message_id(data: Any) -> str | None:
    """Extract a message ID across WAHA engine response variants."""
    if not isinstance(data, dict):
        return None
    direct_id = data.get("id")
    if isinstance(direct_id, str):
        return direct_id
    key = data.get("key")
    if isinstance(key, dict) and isinstance(key.get("id"), str):
        return key["id"]
    raw = data.get("_data")
    if isinstance(raw, dict):
        raw_id = raw.get("id")
        if isinstance(raw_id, dict) and isinstance(raw_id.get("id"), str):
            return raw_id["id"]
    return None


def _error_message(data: Any, status: int) -> str:
    """Extract a useful, non-secret error message."""
    if isinstance(data, dict):
        for key in ("message", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                message = value.get("message")
                if isinstance(message, str) and message:
                    return message
    return f"WAHA request failed with HTTP {status}"


def _optional_string(value: Any) -> str | None:
    """Return a value only when it is a string."""
    return value if isinstance(value, str) else None
