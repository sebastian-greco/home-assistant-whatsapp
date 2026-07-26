"""Config flow for WAHA WhatsApp."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any, override

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntityFilterSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from . import WahaConfigEntry
from .api import (
    WahaAuthenticationError,
    WahaClient,
    WahaConnectionError,
    WahaError,
    WahaServer,
    WahaSession,
    WahaSessionNotFoundError,
)
from .const import (
    CONF_ADDON_SLUG,
    CONF_API_URL,
    CONF_PERSON_ENTITY_ID,
    CONF_RECIPIENT,
    CONF_SESSION,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_API_URL,
    DEFAULT_SESSION,
    DOMAIN,
    SUBENTRY_TYPE_RECIPIENT,
)
from .helpers import normalize_recipient


def _account_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the manual/reconfigure connection form."""
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_API_URL, default=values.get(CONF_API_URL, DEFAULT_API_URL)
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Required(CONF_API_KEY, default=values.get(CONF_API_KEY, "")): (
                TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                )
            ),
            vol.Required(
                CONF_SESSION, default=values.get(CONF_SESSION, DEFAULT_SESSION)
            ): TextSelector(TextSelectorConfig()),
        }
    )


REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        )
    }
)

RECIPIENT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME): TextSelector(TextSelectorConfig()),
        vol.Optional(CONF_PERSON_ENTITY_ID): EntitySelector(
            EntitySelectorConfig(filter=EntityFilterSelectorConfig(domain="person"))
        ),
        vol.Required(CONF_RECIPIENT): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEL)
        ),
    }
)


class WahaWhatsAppConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle configuration for a WAHA server and session."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        """Initialize discovery state."""
        self._discovery_data: dict[str, Any] | None = None
        self._discovery_unique_id: str | None = None

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: WahaConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the recipient subentries supported by this integration."""
        return {SUBENTRY_TYPE_RECIPIENT: RecipientSubentryFlow}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manually connect to a WAHA server."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            data = _normalize_account_data(user_input)
            try:
                server, session = await _validate_account(self.hass, data)
            except WahaAuthenticationError:
                errors["base"] = "invalid_auth"
            except WahaConnectionError:
                errors["base"] = "cannot_connect"
            except WahaSessionNotFoundError as err:
                errors["base"] = "session_not_found"
                placeholders["error"] = str(err)
            except WahaError as err:
                errors["base"] = "waha_error"
                placeholders["error"] = str(err)
            else:
                unique_id = _manual_unique_id(data)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_entry_title(server, session),
                    data=_with_new_webhook_credentials(data),
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _account_schema(), user_input or {}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    @override
    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle automatic discovery from the companion HAOS app."""
        try:
            data = _account_data_from_discovery(discovery_info)
        except KeyError, TypeError, ValueError:
            return self.async_abort(reason="invalid_discovery")

        self._discovery_data = data
        self._discovery_unique_id = f"addon:{discovery_info.slug}:{data[CONF_SESSION]}"
        await self.async_set_unique_id(self._discovery_unique_id)
        self._abort_if_unique_id_configured(updates=data, reload_on_update=False)
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and validate the automatically discovered app."""
        if self._discovery_data is None or self._discovery_unique_id is None:
            return self.async_abort(reason="invalid_discovery")

        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"session": self._discovery_data[CONF_SESSION]}
        if user_input is not None:
            try:
                server, session = await _validate_account(
                    self.hass, self._discovery_data
                )
            except WahaAuthenticationError:
                errors["base"] = "invalid_auth"
            except WahaConnectionError:
                errors["base"] = "cannot_connect"
            except WahaSessionNotFoundError as err:
                errors["base"] = "session_not_found"
                placeholders["error"] = str(err)
            except WahaError as err:
                errors["base"] = "waha_error"
                placeholders["error"] = str(err)
            else:
                return self.async_create_entry(
                    title=_entry_title(server, session),
                    data=_with_new_webhook_credentials(self._discovery_data),
                )

        return self.async_show_form(
            step_id="hassio_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders=placeholders,
        )

    @override
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the WAHA URL, API key, or session."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            data = _normalize_account_data(user_input)
            if CONF_ADDON_SLUG in entry.data:
                data[CONF_ADDON_SLUG] = entry.data[CONF_ADDON_SLUG]
            for key in (CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET):
                if key in entry.data:
                    data[key] = entry.data[key]
            try:
                server, session = await _validate_account(self.hass, data)
            except WahaAuthenticationError:
                errors["base"] = "invalid_auth"
            except WahaConnectionError:
                errors["base"] = "cannot_connect"
            except WahaSessionNotFoundError as err:
                errors["base"] = "session_not_found"
                placeholders["error"] = str(err)
            except WahaError as err:
                errors["base"] = "waha_error"
                placeholders["error"] = str(err)
            else:
                unique_id = entry.unique_id
                if CONF_ADDON_SLUG not in entry.data:
                    unique_id = _manual_unique_id(data)
                return self.async_update_and_abort(
                    entry,
                    unique_id=unique_id,
                    title=_entry_title(server, session),
                    data=data,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _account_schema(entry.data), user_input or entry.data
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace a changed WAHA API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            updated_data = {**entry.data, CONF_API_KEY: user_input[CONF_API_KEY]}
            try:
                server, session = await _validate_account(self.hass, updated_data)
            except WahaAuthenticationError:
                errors["base"] = "invalid_auth"
            except WahaConnectionError:
                errors["base"] = "cannot_connect"
            except WahaError:
                errors["base"] = "waha_error"
            else:
                return self.async_update_and_abort(
                    entry,
                    title=_entry_title(server, session),
                    data=updated_data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
        )


class RecipientSubentryFlow(ConfigSubentryFlow):
    """Create or reconfigure an individual notification recipient."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a WhatsApp recipient."""
        return await self._async_step_recipient(user_input, step_id="user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Modify an existing WhatsApp recipient."""
        return await self._async_step_recipient(
            user_input,
            step_id="reconfigure",
            reconfigure_subentry=self._get_reconfigure_subentry(),
        )

    async def _async_step_recipient(
        self,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        reconfigure_subentry: ConfigSubentry | None = None,
    ) -> SubentryFlowResult:
        """Validate and store an individual contact."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(
                reason="entry_not_loaded",
                description_placeholders={"entry_title": entry.title},
            )

        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            person_entity_id = (
                str(user_input.get(CONF_PERSON_ENTITY_ID, "")).strip() or None
            )
            person_state = (
                self.hass.states.get(person_entity_id)
                if person_entity_id is not None
                else None
            )
            if person_entity_id is not None and (
                person_state is None or person_entity_id.split(".", 1)[0] != "person"
            ):
                errors["base"] = "person_not_found"
            else:
                name = str(user_input.get(CONF_NAME, "")).strip()
                if not name and person_state is not None:
                    name = person_state.name
                if not name:
                    errors["base"] = "invalid_name"
                else:
                    try:
                        recipient = normalize_recipient(user_input[CONF_RECIPIENT])
                    except ValueError as err:
                        errors["base"] = "invalid_recipient"
                        placeholders["error"] = str(err)
                    else:
                        existing = [
                            subentry
                            for subentry in entry.subentries.values()
                            if reconfigure_subentry is None
                            or subentry.subentry_id != reconfigure_subentry.subentry_id
                        ]
                        if person_entity_id is not None and any(
                            subentry.data.get(CONF_PERSON_ENTITY_ID) == person_entity_id
                            for subentry in existing
                        ):
                            return self.async_abort(reason="person_already_configured")
                        if any(
                            subentry.data.get(CONF_RECIPIENT) == recipient
                            for subentry in existing
                        ):
                            return self.async_abort(reason="phone_already_configured")

                        data = {CONF_RECIPIENT: recipient}
                        if person_entity_id is not None:
                            data[CONF_PERSON_ENTITY_ID] = person_entity_id
                        if reconfigure_subentry is not None:
                            return self.async_update_and_abort(
                                entry,
                                reconfigure_subentry,
                                title=name,
                                data=data,
                            )
                        return self.async_create_entry(
                            title=name,
                            data=data,
                        )

        suggested_values = dict(user_input or {})
        if user_input is None and reconfigure_subentry is not None:
            suggested_values = {
                **reconfigure_subentry.data,
                CONF_NAME: reconfigure_subentry.title,
            }

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                RECIPIENT_SCHEMA, suggested_values
            ),
            errors=errors,
            description_placeholders=placeholders,
        )


async def _validate_account(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[WahaServer, WahaSession]:
    """Validate a WAHA API connection and configured session."""
    client = WahaClient(
        async_get_clientsession(hass),
        data[CONF_API_KEY],
        data[CONF_SESSION],
        base_url=data[CONF_API_URL],
    )
    server = await client.async_get_server()
    session = await client.async_get_session()
    return server, session


def _normalize_account_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize user-entered connection data."""
    return {
        CONF_API_URL: str(data[CONF_API_URL]).strip().rstrip("/"),
        CONF_API_KEY: str(data[CONF_API_KEY]),
        CONF_SESSION: str(data[CONF_SESSION]).strip(),
    }


def _with_new_webhook_credentials(data: Mapping[str, Any]) -> dict[str, Any]:
    """Create private callback credentials for a new config entry."""
    return {
        **data,
        CONF_WEBHOOK_ID: secrets.token_hex(32),
        CONF_WEBHOOK_SECRET: secrets.token_urlsafe(48),
    }


def _account_data_from_discovery(info: HassioServiceInfo) -> dict[str, Any]:
    """Convert Supervisor discovery data into a config entry payload."""
    host = str(info.config["host"]).strip()
    port = int(info.config.get("port", 3000))
    if not host or not 1 <= port <= 65535:
        raise ValueError("Invalid discovery host or port")
    return {
        CONF_API_URL: f"http://{host}:{port}",
        CONF_API_KEY: str(info.config["api_key"]),
        CONF_SESSION: str(info.config.get("session", DEFAULT_SESSION)).strip(),
        CONF_ADDON_SLUG: info.slug,
    }


def _manual_unique_id(data: Mapping[str, Any]) -> str:
    """Create a stable unique ID for a manually configured session."""
    return f"manual:{data[CONF_API_URL]}:{data[CONF_SESSION]}"


def _entry_title(server: WahaServer, session: WahaSession) -> str:
    """Build a useful entry title without exposing credentials."""
    identity = session.push_name or session.name
    return f"WAHA {identity} ({server.version})"
