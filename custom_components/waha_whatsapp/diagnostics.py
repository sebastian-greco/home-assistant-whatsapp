"""Diagnostics for the WAHA WhatsApp integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from . import WahaConfigEntry
from .const import CONF_RECIPIENT

TO_REDACT = {CONF_API_KEY, CONF_RECIPIENT, "account_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WahaConfigEntry
) -> dict[str, Any]:
    """Return redacted server, session, and recipient diagnostics."""
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "server": asdict(entry.runtime_data.server),
        "session": async_redact_data(asdict(entry.runtime_data.session), TO_REDACT),
        "recipients": [
            {
                "title": subentry.title,
                "data": async_redact_data(dict(subentry.data), TO_REDACT),
            }
            for subentry in entry.subentries.values()
        ],
    }
