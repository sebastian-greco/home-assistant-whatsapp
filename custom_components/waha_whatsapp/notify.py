"""Individual and household-group notify entities for WAHA WhatsApp."""

from typing import override

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WahaConfigEntry
from .api import WahaError
from .const import (
    CONF_CONTACT_ROLE,
    CONF_GROUP_ADULTS,
    CONF_RECIPIENT,
    CONTACT_ROLE_FAMILY,
    CONTACT_ROLE_GUEST,
    DOMAIN,
    RECIPIENT_GROUP_ADULTS,
    RECIPIENT_GROUP_FAMILY,
    RECIPIENT_GROUP_GUESTS,
)
from .helpers import render_notification


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WahaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up person recipients and logical household groups."""
    recipients = list(config_entry.subentries.items())
    for subentry_id, subentry in recipients:
        async_add_entities(
            [WahaNotifyEntity(config_entry, subentry)],
            config_subentry_id=subentry_id,
        )

    group_members = {
        RECIPIENT_GROUP_FAMILY: [
            subentry
            for _, subentry in recipients
            if subentry.data.get(CONF_CONTACT_ROLE, CONTACT_ROLE_FAMILY)
            == CONTACT_ROLE_FAMILY
        ],
        RECIPIENT_GROUP_ADULTS: [
            subentry
            for _, subentry in recipients
            if subentry.data.get(CONF_GROUP_ADULTS, False)
        ],
        RECIPIENT_GROUP_GUESTS: [
            subentry
            for _, subentry in recipients
            if subentry.data.get(CONF_CONTACT_ROLE, CONTACT_ROLE_FAMILY)
            == CONTACT_ROLE_GUEST
        ],
    }
    async_add_entities(
        [
            WahaGroupNotifyEntity(config_entry, group, members)
            for group, members in group_members.items()
        ]
    )


class WahaNotifyEntity(NotifyEntity):
    """A person-linked WhatsApp notification destination."""

    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(
        self, config_entry: WahaConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the recipient notify entity."""
        self.config_entry = config_entry
        self._subentry = subentry
        self._attr_name = subentry.title
        self._attr_unique_id = (
            f"{config_entry.unique_id}_{subentry.unique_id or subentry.subentry_id}"
        )
        self._attr_device_info = _device_info(config_entry)

    @property
    @override
    def suggested_object_id(self) -> str:
        """Use the Home Assistant Person name for the initial entity ID."""
        return self._subentry.title

    @override
    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a free-form notification to this person."""
        try:
            await _async_send_to_recipient(
                self.config_entry, self._subentry, message, title
            )
        except WahaError as err:
            raise _home_assistant_error(err) from err


class WahaGroupNotifyEntity(NotifyEntity):
    """A logical group that fans out to selected WhatsApp recipients."""

    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(
        self,
        config_entry: WahaConfigEntry,
        group: str,
        members: list[ConfigSubentry],
    ) -> None:
        """Initialize a Family, Adults, or Guests notification group."""
        self.config_entry = config_entry
        self._group = group
        self._members = members
        self._attr_name = group.title()
        self._attr_unique_id = f"{config_entry.unique_id}_group_{group}"
        self._attr_device_info = _device_info(config_entry)

    @property
    @override
    def suggested_object_id(self) -> str:
        """Use the logical group for the initial entity ID."""
        return self._group

    @override
    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send independently to every person selected for this group."""
        failures: list[WahaError] = []
        for member in self._members:
            try:
                await _async_send_to_recipient(
                    self.config_entry, member, message, title
                )
            except WahaError as err:
                failures.append(err)
        if failures:
            first_error = failures[0]
            error = WahaError(
                f"{len(failures)} of {len(self._members)} group deliveries failed: "
                f"{first_error}"
            )
            raise _home_assistant_error(error) from first_error


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


def _device_info(config_entry: WahaConfigEntry) -> DeviceInfo:
    """Describe the shared local WAHA service."""
    server = config_entry.runtime_data.server
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.unique_id or config_entry.entry_id)},
        name=config_entry.title,
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
