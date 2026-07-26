"""Pure helpers for WAHA WhatsApp config-entry migrations."""

from collections.abc import Mapping
from typing import Any

OBSOLETE_GROUP_CONFIG_KEYS = frozenset({"contact_role", "group_adults"})
LEGACY_GROUP_NAMES = ("family", "adults", "guests")


def without_obsolete_group_config(data: Mapping[str, Any]) -> dict[str, Any]:
    """Copy config data without the legacy household-group fields."""
    return {
        key: value
        for key, value in data.items()
        if key not in OBSOLETE_GROUP_CONFIG_KEYS
    }


def legacy_group_unique_ids(config_entry_unique_id: str | None) -> frozenset[str]:
    """Return the exact integration-owned unique IDs used by legacy groups."""
    return frozenset(
        f"{config_entry_unique_id}_group_{group}" for group in LEGACY_GROUP_NAMES
    )


def with_webhook_credentials(
    data: Mapping[str, Any], webhook_id: str, webhook_secret: str
) -> dict[str, Any]:
    """Add missing private webhook credentials without rotating existing values."""
    migrated = dict(data)
    migrated.setdefault("webhook_id", webhook_id)
    migrated.setdefault("webhook_secret", webhook_secret)
    return migrated
