"""Tests for recipient validation."""

import pytest

from ._loader import load_integration_module

helpers = load_integration_module("helpers")


def test_normalize_phone() -> None:
    """Common phone formatting is removed before sending."""
    assert helpers.normalize_recipient("+39 333-123-4567") == "393331234567"
    assert helpers.recipient_chat_id("+39 333-123-4567") == "393331234567@c.us"


def test_existing_chat_id_is_preserved() -> None:
    """Direct WAHA chat IDs are accepted by low-level API calls."""
    assert helpers.normalize_chat_id("393331234567@c.us") == "393331234567@c.us"


def test_notification_title_is_whatsapp_readable() -> None:
    """Home Assistant titles become a bold heading above the message."""
    assert helpers.render_notification("Door open", "Garage") == (
        "*Garage*\n\nDoor open"
    )
    assert helpers.render_notification("Door open") == "Door open"


@pytest.mark.parametrize("recipient", ["+39", "abc123", "1" * 16])
def test_invalid_phone(recipient: str) -> None:
    """Malformed phone recipients fail locally."""
    with pytest.raises(ValueError, match="7 to 15 digits"):
        helpers.normalize_recipient(recipient)
