"""WAHA WhatsApp integration for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WahaAuthenticationError,
    WahaClient,
    WahaError,
    WahaMessage,
    WahaServer,
    WahaSession,
)
from .const import (
    ATTR_LINK_PREVIEW,
    ATTR_MESSAGE,
    ATTR_TITLE,
    ATTR_TO,
    CONF_API_URL,
    CONF_SESSION,
    DOMAIN,
    SERVICE_SEND_MESSAGE,
)
from .helpers import normalize_recipient, render_notification

PLATFORMS: list[Platform] = [Platform.NOTIFY]
CONF_CONFIG_ENTRY_ID = "config_entry_id"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_TO): cv.string,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_TITLE): cv.string,
        vol.Optional(ATTR_LINK_PREVIEW, default=True): cv.boolean,
    }
)


@dataclass(slots=True)
class WahaRuntimeData:
    """Runtime data for a configured WAHA server and session."""

    client: WahaClient
    server: WahaServer
    session: WahaSession


type WahaConfigEntry = ConfigEntry[WahaRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-level WAHA actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        _async_handle_send_message,
        schema=SEND_MESSAGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WahaConfigEntry) -> bool:
    """Set up a WAHA WhatsApp config entry."""
    client = WahaClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        entry.data[CONF_SESSION],
        base_url=entry.data[CONF_API_URL],
    )

    try:
        server = await client.async_get_server()
        session = await client.async_get_session()
    except WahaAuthenticationError as err:
        raise ConfigEntryAuthFailed("WAHA rejected the configured API key") from err
    except WahaError as err:
        raise ConfigEntryNotReady(f"Unable to connect to WAHA: {err}") from err

    entry.runtime_data = WahaRuntimeData(client, server, session)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WahaConfigEntry) -> bool:
    """Unload a WAHA WhatsApp config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_handle_send_message(call: ServiceCall) -> ServiceResponse:
    """Send one free-form message to an arbitrary phone number."""
    entry = _loaded_entry(call.hass, call.data[CONF_CONFIG_ENTRY_ID])
    try:
        recipient = normalize_recipient(call.data[ATTR_TO])
        result = await entry.runtime_data.client.async_send_text(
            recipient,
            render_notification(call.data[ATTR_MESSAGE], call.data.get(ATTR_TITLE)),
            link_preview=call.data[ATTR_LINK_PREVIEW],
        )
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_recipient",
            translation_placeholders={"error": str(err)},
        ) from err
    except WahaError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="action_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    if call.return_response:
        return _service_response(result)
    return None


def _loaded_entry(hass: HomeAssistant, entry_id: str) -> WahaConfigEntry:
    """Resolve and validate the config entry selected by an action."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_config_entry",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"entry_title": entry.title},
        )
    return cast(WahaConfigEntry, entry)


def _service_response(result: WahaMessage) -> dict[str, str]:
    """Format an action response for Home Assistant."""
    response = {"chat_id": result.chat_id}
    if result.id is not None:
        response["message_id"] = result.id
    return response
