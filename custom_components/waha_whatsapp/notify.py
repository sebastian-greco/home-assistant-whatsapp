"""Individual notify entities for WAHA WhatsApp."""

from typing import override

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WahaConfigEntry
from .api import WahaError
from .const import CONF_RECIPIENT, DOMAIN
from .helpers import contact_state_attributes, render_notification


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WahaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up individual WhatsApp recipients."""
    for subentry_id, subentry in config_entry.subentries.items():
        async_add_entities(
            [WahaNotifyEntity(config_entry, subentry)],
            config_subentry_id=subentry_id,
        )


class WahaNotifyEntity(NotifyEntity):
    """An individual WhatsApp notification destination."""

    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, config_entry: WahaConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the recipient notify entity."""
        self.config_entry = config_entry
        self._subentry = subentry
        self._attr_name = subentry.title
        self._attr_unique_id = (
            f"{config_entry.unique_id}_{subentry.unique_id or subentry.subentry_id}"
        )
        self._attr_device_info = _device_info(config_entry, subentry)
        self._attr_extra_state_attributes = contact_state_attributes(subentry.data)

    @property
    @override
    def suggested_object_id(self) -> str:
        """Use the configured contact name for the initial entity ID."""
        return self._subentry.title

    @override
    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a free-form notification to this contact."""
        try:
            await _async_send_to_recipient(
                self.config_entry, self._subentry, message, title
            )
        except WahaError as err:
            raise _home_assistant_error(err) from err


async def _async_send_to_recipient(
    config_entry: WahaConfigEntry,
    subentry: ConfigSubentry,
    message: str,
    title: str | None,
) -> None:
    """Render and send one configured recipient notification."""
    await config_entry.runtime_data.client.async_send_text(
        subentry.data[CONF_RECIPIENT],
        render_notification(message, title),
    )


def _device_info(
    config_entry: WahaConfigEntry, subentry: ConfigSubentry
) -> DeviceInfo:
    """Describe the local WAHA service for one recipient subentry.

    Home Assistant 2026.8 associates a device with a single config
    subentry. Recipient entities are individual subentries, so sharing the
    old account-level identifier makes the device move between recipients
    during entity registration.
    """
    server = config_entry.runtime_data.server
    subentry_id = subentry.unique_id or subentry.subentry_id
    return DeviceInfo(
        identifiers={
            (
                DOMAIN,
                f"{config_entry.unique_id or config_entry.entry_id}:{subentry_id}",
            )
        },
        name="WAHA",
        manufacturer="WAHA",
        model=f"WhatsApp HTTP API ({server.engine or 'unknown engine'})",
        sw_version=server.version,
        entry_type=DeviceEntryType.SERVICE,
    )


def _home_assistant_error(err: Exception) -> HomeAssistantError:
    """Translate a WAHA delivery failure for Home Assistant."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="action_failed",
        translation_placeholders={"error": str(err)},
    )
