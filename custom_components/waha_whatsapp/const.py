"""Constants for the WAHA WhatsApp integration."""

from typing import Final

DOMAIN: Final = "waha_whatsapp"

DEFAULT_API_URL: Final = "http://localhost:3000"
DEFAULT_SESSION: Final = "default"

CONF_API_URL: Final = "api_url"
CONF_SESSION: Final = "session"
CONF_PERSON_ENTITY_ID: Final = "person_entity_id"
CONF_RECIPIENT: Final = "recipient"
CONF_ADDON_SLUG: Final = "addon_slug"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"

SUBENTRY_TYPE_RECIPIENT: Final = "recipient"

ATTR_TO: Final = "to"
ATTR_TITLE: Final = "title"
ATTR_MESSAGE: Final = "message"
ATTR_LINK_PREVIEW: Final = "link_preview"
ATTR_PERSON_ENTITY_ID: Final = "person_entity_id"
ATTR_ACTIONS: Final = "actions"
ATTR_ENTITY_ID: Final = "entity_id"
ATTR_NO_ACTION_TITLE: Final = "no_action_title"
ATTR_SETTLE_SECONDS: Final = "settle_seconds"

SERVICE_SEND_MESSAGE: Final = "send_message"
SERVICE_SEND_POLL: Final = "send_poll"

DEFAULT_NO_ACTION_TITLE: Final = "No action"
DEFAULT_SETTLE_SECONDS: Final = 5.0
EVENT_MOBILE_APP_NOTIFICATION_ACTION: Final = "mobile_app_notification_action"

SESSION_STATUS_WORKING: Final = "WORKING"
