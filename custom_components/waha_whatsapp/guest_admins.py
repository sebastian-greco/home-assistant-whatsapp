"""Discover explicitly mapped, human Home Assistant administrators for setup.

This is a bootstrap snapshot, not a background role synchronizer. The caller
must review it with the user before any WhatsApp group write.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import CONF_PERSON_ENTITY_ID, CONF_RECIPIENT, SUBENTRY_TYPE_RECIPIENT
from .helpers import normalize_recipient, recipient_chat_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True)
class GuestAdminContact:
    """One entry-local recipient mapped to an active human HA administrator."""

    user_id: str
    name: str
    person_entity_id: str
    recipient: str
    chat_id: str


@dataclass(frozen=True, slots=True)
class GuestAdminGap:
    """An HA administrator without one unambiguous entry-local WA recipient."""

    user_id: str
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class GuestAdminPreview:
    """Reviewable provisioning candidates and gaps for one config entry."""

    contacts: tuple[GuestAdminContact, ...]
    gaps: tuple[GuestAdminGap, ...]


async def async_guest_admin_preview(
    hass: HomeAssistant, entry: ConfigEntry
) -> GuestAdminPreview:
    """Map active, non-system HA admins to this entry's individual contacts.

    Never inspect another WAHA entry. Do not infer identity from matching names
    or WhatsApp profile labels. A Person may be associated only through its
    existing ``user_id`` state attribute and the contact's explicit Person ID.
    """
    users = await hass.auth.async_get_users()
    admin_users = [
        user
        for user in users
        if getattr(user, "is_admin", False)
        and getattr(user, "is_active", False)
        and not getattr(user, "is_system_generated", False)
        and isinstance(getattr(user, "id", None), str)
    ]
    person_ids_by_user: dict[str, list[str]] = {}
    for state in hass.states.async_all():
        entity_id = getattr(state, "entity_id", "")
        if not isinstance(entity_id, str) or not entity_id.startswith("person."):
            continue
        user_id = getattr(state, "attributes", {}).get("user_id")
        if isinstance(user_id, str) and user_id:
            person_ids_by_user.setdefault(user_id, []).append(entity_id)

    recipient_by_person: dict[str, list[str]] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_RECIPIENT:
            continue
        person_id = subentry.data.get(CONF_PERSON_ENTITY_ID)
        raw_recipient = subentry.data.get(CONF_RECIPIENT)
        if not isinstance(person_id, str) or not isinstance(raw_recipient, str):
            continue
        try:
            recipient = normalize_recipient(raw_recipient)
        except ValueError:
            continue
        recipient_by_person.setdefault(person_id, []).append(recipient)

    contacts: list[GuestAdminContact] = []
    gaps: list[GuestAdminGap] = []
    for user in sorted(admin_users, key=lambda item: item.id):
        name = _safe_user_name(user)
        person_ids = person_ids_by_user.get(user.id, [])
        if len(person_ids) != 1:
            reason = "no_person" if not person_ids else "multiple_persons"
            gaps.append(GuestAdminGap(user.id, name, reason))
            continue
        person_id = person_ids[0]
        recipients = recipient_by_person.get(person_id, [])
        if len(recipients) != 1:
            reason = "no_contact" if not recipients else "multiple_contacts"
            gaps.append(GuestAdminGap(user.id, name, reason))
            continue
        recipient = recipients[0]
        contacts.append(
            GuestAdminContact(
                user.id,
                name,
                person_id,
                recipient,
                recipient_chat_id(recipient),
            )
        )

    # Duplicate phone numbers across two admin users are not independent
    # WhatsApp identities, even if config subentries were edited outside flow.
    counts: dict[str, int] = {}
    for contact in contacts:
        counts[contact.chat_id] = counts.get(contact.chat_id, 0) + 1
    unambiguous = []
    for contact in contacts:
        if counts[contact.chat_id] == 1:
            unambiguous.append(contact)
        else:
            gaps.append(GuestAdminGap(contact.user_id, contact.name, "shared_contact"))
    return GuestAdminPreview(tuple(unambiguous), tuple(gaps))


def _safe_user_name(user: Any) -> str:
    """Return a UI label only; it is never an identity or authorization key."""
    name = getattr(user, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "Unnamed administrator"


def reviewed_admin_fingerprint(
    preview: GuestAdminPreview, excluded_user_ids: Collection[str], secret: str
) -> str:
    """Bind the reviewed host mapping without copying phone numbers to options.

    The private webhook secret makes the fingerprint resistant to guessing a
    configured phone number from its digest. A changed Person or phone mapping
    requires another explicit setup review before an external group write.
    """
    if not isinstance(secret, str) or not secret:
        raise ValueError("WAHA entry secret is missing")
    rows = sorted(
        (contact.user_id, contact.person_entity_id, contact.recipient)
        for contact in preview.contacts
    )
    excluded = sorted(set(excluded_user_ids))
    if not all(isinstance(user_id, str) and user_id for user_id in excluded):
        raise ValueError("Invalid excluded administrator")
    gaps = sorted((gap.user_id, gap.reason) for gap in preview.gaps)
    material = (
        "mapped\n"
        + "\n".join("\0".join(row) for row in rows)
        + "\ngaps\n"
        + "\n".join("\0".join(row) for row in gaps)
        + "\nexcluded\n"
        + "\n".join(excluded)
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
