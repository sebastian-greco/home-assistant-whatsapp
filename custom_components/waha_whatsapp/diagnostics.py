"""Diagnostics for the WAHA WhatsApp integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from . import WahaConfigEntry
from .const import CONF_RECIPIENT, CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET

TO_REDACT = {
    CONF_API_KEY,
    CONF_RECIPIENT,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    "account_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WahaConfigEntry
) -> dict[str, Any]:
    """Return redacted server, session, and recipient diagnostics."""
    guest = entry.runtime_data.guest_manager.snapshot()
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "server": asdict(entry.runtime_data.server),
        "session": async_redact_data(asdict(entry.runtime_data.session), TO_REDACT),
        "actionable_polls": {
            "pending": entry.runtime_data.poll_manager.pending_count,
            "failed_votes": entry.runtime_data.poll_manager.failed_vote_count,
            "rejected_votes": entry.runtime_data.poll_manager.rejected_vote_count,
            "rejection_reasons": (
                entry.runtime_data.poll_manager.rejected_vote_reasons
            ),
            "webhook_configured": True,
        },
        "inbound_channel": {
            "accepted": entry.runtime_data.inbound_manager.accepted_count,
            "rejected": entry.runtime_data.inbound_manager.rejected_event_count,
            "rejection_reasons": (entry.runtime_data.inbound_manager.rejection_reasons),
            "persistence_available": (
                entry.runtime_data.channel_registry.persistence_available
            ),
            "webhook_configured": True,
        },
        "managed_guest_group": {
            "ready": guest["ready"],
            "confirmed": guest["confirmed"],
            "provisioning_status": guest["provisioning_status"],
            "failure_reason": guest["failure_reason"],
            "revision": guest["revision"],
            "observed_at": guest["observed_at"],
            "active_count": sum(
                member["status"] == "active" for member in guest["memberships"]
            ),
            "closed_count": sum(
                member["status"] == "left" for member in guest["memberships"]
            ),
        },
        "recipients": [
            {
                "title": subentry.title,
                "data": async_redact_data(dict(subentry.data), TO_REDACT),
            }
            for subentry in entry.subentries.values()
        ],
    }
