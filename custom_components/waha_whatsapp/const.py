"""Constants for the WAHA WhatsApp integration."""

from typing import Final

DOMAIN: Final = "waha_whatsapp"

DEFAULT_API_URL: Final = "http://localhost:3000"
DEFAULT_SESSION: Final = "default"

CONF_API_URL: Final = "api_url"
CONF_SESSION: Final = "session"
CONF_PERSON_ENTITY_ID: Final = "person_entity_id"
CONF_RECIPIENT: Final = "recipient"
CONF_CONTACT_ROLE: Final = "contact_role"
CONF_GROUP_ADULTS: Final = "group_adults"
CONF_ADDON_SLUG: Final = "addon_slug"

RECIPIENT_GROUP_FAMILY: Final = "family"
RECIPIENT_GROUP_ADULTS: Final = "adults"
RECIPIENT_GROUP_GUESTS: Final = "guests"

CONTACT_ROLE_FAMILY: Final = "family"
CONTACT_ROLE_GUEST: Final = "guest"
CONTACT_ROLES: Final = (CONTACT_ROLE_FAMILY, CONTACT_ROLE_GUEST)

SUBENTRY_TYPE_RECIPIENT: Final = "recipient"

ATTR_TO: Final = "to"
ATTR_TITLE: Final = "title"
ATTR_MESSAGE: Final = "message"
ATTR_LINK_PREVIEW: Final = "link_preview"

SERVICE_SEND_MESSAGE: Final = "send_message"

SESSION_STATUS_WORKING: Final = "WORKING"
