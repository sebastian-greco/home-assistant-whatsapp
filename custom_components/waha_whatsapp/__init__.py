"""WAHA WhatsApp integration for Home Assistant."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from http import HTTPStatus
from typing import cast

import voluptuous as vol
from aiohttp.web import Request, Response
from homeassistant.components import webhook
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
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WahaAuthenticationError,
    WahaClient,
    WahaError,
    WahaMessage,
    WahaResponseError,
    WahaServer,
    WahaSession,
)
from .const import (
    ATTR_ACTIONS,
    ATTR_ENTITY_ID,
    ATTR_LINK_PREVIEW,
    ATTR_MESSAGE,
    ATTR_NO_ACTION_TITLE,
    ATTR_SETTLE_SECONDS,
    ATTR_TITLE,
    ATTR_TO,
    CONF_ADDON_SLUG,
    CONF_API_URL,
    CONF_RECIPIENT,
    CONF_SESSION,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_NO_ACTION_TITLE,
    DEFAULT_SETTLE_SECONDS,
    DOMAIN,
    SERVICE_SEND_MESSAGE,
    SERVICE_SEND_POLL,
)
from .helpers import normalize_recipient, render_notification
from .migration import (
    legacy_group_unique_ids,
    with_webhook_credentials,
    without_obsolete_group_config,
)
from .poll_manager import WahaPollManager
from .polls import PollValidationError, build_poll_options

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

POLL_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("action"): cv.string,
        vol.Required("title"): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

SEND_POLL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_TITLE): cv.string,
        vol.Required(ATTR_ACTIONS): vol.All(cv.ensure_list, [POLL_ACTION_SCHEMA]),
        vol.Optional(ATTR_SETTLE_SECONDS, default=DEFAULT_SETTLE_SECONDS): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=30)
        ),
        vol.Optional(ATTR_NO_ACTION_TITLE, default=DEFAULT_NO_ACTION_TITLE): cv.string,
    }
)


@dataclass(slots=True)
class WahaRuntimeData:
    """Runtime data for a configured WAHA server and session."""

    client: WahaClient
    server: WahaServer
    session: WahaSession
    poll_manager: WahaPollManager


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
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_POLL,
        _async_handle_send_poll,
        schema=SEND_POLL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: WahaConfigEntry) -> bool:
    """Migrate legacy groups and add stable private webhook credentials."""
    if entry.version != 1:
        return False

    migrated_data = dict(entry.data)
    migrated_options = dict(entry.options)

    if entry.minor_version < 2:
        for subentry in tuple(entry.subentries.values()):
            migrated_subentry_data = without_obsolete_group_config(subentry.data)
            if migrated_subentry_data != subentry.data:
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=migrated_subentry_data
                )

        entity_registry = er.async_get(hass)
        obsolete_unique_ids = legacy_group_unique_ids(entry.unique_id)
        for entity in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if (
                entity.domain == Platform.NOTIFY
                and entity.platform == DOMAIN
                and entity.unique_id in obsolete_unique_ids
            ):
                entity_registry.async_remove(entity.entity_id)

        migrated_data = without_obsolete_group_config(migrated_data)
        migrated_options = without_obsolete_group_config(migrated_options)

    if entry.minor_version < 3:
        migrated_data = with_webhook_credentials(
            migrated_data,
            secrets.token_hex(32),
            secrets.token_urlsafe(48),
        )
        hass.config_entries.async_update_entry(
            entry,
            data=migrated_data,
            options=migrated_options,
            version=1,
            minor_version=3,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: WahaConfigEntry) -> bool:
    """Set up a WAHA WhatsApp config entry."""
    _ensure_webhook_credentials(hass, entry)
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

    poll_manager = WahaPollManager(
        hass,
        entry,
        client,
        entry.data[CONF_WEBHOOK_SECRET],
    )
    await poll_manager.async_start()
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    webhook.async_register(
        hass,
        DOMAIN,
        f"WAHA poll actions ({entry.title})",
        webhook_id,
        lambda callback_hass, callback_id, request: _async_handle_poll_webhook(
            poll_manager, callback_hass, callback_id, request
        ),
        local_only=True,
    )
    try:
        webhook_url = _webhook_url(hass, entry, webhook_id)
        await client.async_ensure_webhook(webhook_url, entry.data[CONF_WEBHOOK_SECRET])
    except WahaError as err:
        webhook.async_unregister(hass, webhook_id)
        poll_manager.stop()
        raise ConfigEntryNotReady(
            f"Unable to configure the private WAHA poll webhook: {err}"
        ) from err
    except Exception:
        webhook.async_unregister(hass, webhook_id)
        poll_manager.stop()
        raise

    entry.runtime_data = WahaRuntimeData(client, server, session, poll_manager)
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))
    entry.async_on_unload(poll_manager.stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WahaConfigEntry) -> bool:
    """Unload a WAHA WhatsApp config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: WahaConfigEntry) -> None:
    """Reload individual recipient entities after entry changes."""
    await hass.config_entries.async_reload(entry.entry_id)


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


async def _async_handle_send_poll(call: ServiceCall) -> ServiceResponse:
    """Send an actionable poll to one configured individual contact."""
    entry, recipient = _configured_recipient(call.hass, call.data[ATTR_ENTITY_ID])
    try:
        options = build_poll_options(
            call.data[ATTR_ACTIONS], call.data[ATTR_NO_ACTION_TITLE]
        )
        result = await entry.runtime_data.client.async_send_poll(
            recipient,
            _poll_question(call.data[ATTR_MESSAGE], call.data.get(ATTR_TITLE)),
            [option.title for option in options],
        )
        if result.id is None:
            raise WahaResponseError("WAHA did not return the poll message ID")
        await entry.runtime_data.poll_manager.async_register_poll(
            result.id,
            result.chat_id,
            options,
            call.data[ATTR_SETTLE_SECONDS],
        )
    except PollValidationError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_poll",
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


def _configured_recipient(
    hass: HomeAssistant, entity_id: str
) -> tuple[WahaConfigEntry, str]:
    """Resolve an integration-owned notify entity to its private recipient."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if (
        registry_entry is None
        or registry_entry.domain != Platform.NOTIFY
        or registry_entry.platform != DOMAIN
        or registry_entry.config_entry_id is None
        or registry_entry.config_subentry_id is None
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_notify_entity",
            translation_placeholders={"entity_id": entity_id},
        )
    entry = _loaded_entry(hass, registry_entry.config_entry_id)
    subentry = entry.subentries.get(registry_entry.config_subentry_id)
    if subentry is None or CONF_RECIPIENT not in subentry.data:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_notify_entity",
            translation_placeholders={"entity_id": entity_id},
        )
    return entry, subentry.data[CONF_RECIPIENT]


def _poll_question(message: str, title: str | None) -> str:
    """Render a poll question without WhatsApp markdown side effects."""
    return f"{title.strip()}\n\n{message}" if title and title.strip() else message


def _ensure_webhook_credentials(hass: HomeAssistant, entry: WahaConfigEntry) -> None:
    """Repair entries missing credentials without rotating valid callbacks."""
    migrated = with_webhook_credentials(
        entry.data,
        secrets.token_hex(32),
        secrets.token_urlsafe(48),
    )
    if migrated != entry.data:
        hass.config_entries.async_update_entry(entry, data=migrated)


def _webhook_url(hass: HomeAssistant, entry: WahaConfigEntry, webhook_id: str) -> str:
    """Build the private callback URL reachable from the configured WAHA host."""
    if CONF_ADDON_SLUG in entry.data:
        return f"http://homeassistant:8123{webhook.async_generate_path(webhook_id)}"
    return webhook.async_generate_url(
        hass,
        webhook_id,
        allow_external=False,
        prefer_external=False,
    )


async def _async_handle_poll_webhook(
    manager: WahaPollManager,
    hass: HomeAssistant,
    webhook_id: str,
    request: Request,
) -> Response:
    """Authenticate and accept an internal WAHA poll event."""
    del hass, webhook_id
    raw_body = await request.read()
    algorithm = request.headers.get("X-Webhook-Hmac-Algorithm", "").lower()
    signature = request.headers.get("X-Webhook-Hmac")
    if algorithm != "sha512" or not manager.verify_signature(raw_body, signature):
        return Response(status=HTTPStatus.UNAUTHORIZED)
    payload = manager.decode_payload(raw_body)
    if payload is None:
        return Response(status=HTTPStatus.BAD_REQUEST)
    await manager.async_handle_payload(payload)
    return Response(status=HTTPStatus.OK)


def _service_response(result: WahaMessage) -> dict[str, str]:
    """Format an action response for Home Assistant."""
    response = {"chat_id": result.chat_id}
    if result.id is not None:
        response["message_id"] = result.id
    return response
