"""Explicit, inert command declarations for authenticated inbound text.

Definitions only select messages to publish as command requests. They never
contain Home Assistant actions, templates, or WhatsApp destinations.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import CHANNEL_SCHEMA_VERSION, EVENT_WAHA_WHATSAPP

if TYPE_CHECKING:
    from homeassistant.core import Context, HomeAssistant

STORAGE_VERSION = 1
MAX_COMMANDS = 32
MAX_ALIASES = 5
MAX_CONTACTS = 64
MAX_COMMAND_TEXT = 256
MAX_ARGUMENTS = 8
MAX_ARGUMENT_LENGTH = 80
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_INVOCATION = re.compile(
    r"/([a-z][a-z0-9_]{0,31})(?:\s+(.+))?\Z", re.S | re.I | re.ASCII
)


class CommandRegistryError(ValueError):
    """A command declaration is invalid or its private Store is unavailable."""


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    """A named command and the identities allowed to request it."""

    name: str
    aliases: tuple[str, ...]
    contact_ids: tuple[str, ...]
    allow_current_guests: bool

    def as_storage(self) -> dict[str, Any]:
        """Return metadata only; no phone numbers or message content."""
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "contact_ids": list(self.contact_ids),
            "allow_current_guests": self.allow_current_guests,
        }


def _name(value: object) -> str:
    """Normalize an ASCII command name without silently changing its meaning."""
    if not isinstance(value, str):
        raise CommandRegistryError("Command names must be strings")
    normalized = value.strip().lower()
    if not value.isascii() or _NAME.fullmatch(normalized) is None:
        raise CommandRegistryError(
            "Command names must be 1-32 ASCII letters, digits, or underscores"
        )
    return normalized


def _definition(
    name: object,
    aliases: object,
    contact_ids: object,
    allow_current_guests: object,
) -> CommandDefinition:
    """Validate a bounded, positive recipient allowlist."""
    canonical = _name(name)
    if not isinstance(aliases, list | tuple) or len(aliases) > MAX_ALIASES:
        raise CommandRegistryError("Invalid command aliases")
    normalized_aliases = tuple(_name(alias) for alias in aliases)
    if len(set((canonical, *normalized_aliases))) != 1 + len(normalized_aliases):
        raise CommandRegistryError("Command aliases must be unique")
    if not isinstance(contact_ids, list | tuple) or len(contact_ids) > MAX_CONTACTS:
        raise CommandRegistryError("Invalid allowed contacts")
    if any(
        not isinstance(item, str) or not item or len(item) > 128 for item in contact_ids
    ):
        raise CommandRegistryError("Invalid allowed contacts")
    normalized_contacts = tuple(dict.fromkeys(contact_ids))
    if not isinstance(allow_current_guests, bool):
        raise CommandRegistryError("Invalid guest permission")
    if not normalized_contacts and not allow_current_guests:
        raise CommandRegistryError("A command needs at least one allowed sender")
    return CommandDefinition(
        canonical,
        normalized_aliases,
        normalized_contacts,
        allow_current_guests,
    )


class CommandRegistry:
    """Persist declarations and publish requests from the trusted webhook path."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        store: Any | None = None,
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        if store is None:
            from homeassistant.helpers.storage import Store

            store = Store[dict[str, Any]](
                hass,
                STORAGE_VERSION,
                f"waha_whatsapp.commands.{entry_id}",
                private=True,
                atomic_writes=True,
            )
        self._store = store
        self._lock = asyncio.Lock()
        self._commands: dict[str, CommandDefinition] = {}
        self.available = False
        self.failure_reason: str | None = None
        self.requested_count = 0

    async def async_start(self) -> None:
        """Restore only a fully valid snapshot; corrupted state fails closed."""
        try:
            raw = await self._store.async_load()
            if raw is None:
                definitions: dict[str, CommandDefinition] = {}
            else:
                if (
                    not isinstance(raw, Mapping)
                    or raw.get("version") != STORAGE_VERSION
                ):
                    raise CommandRegistryError("Invalid command Store version")
                items = raw.get("commands")
                if not isinstance(items, list) or len(items) > MAX_COMMANDS:
                    raise CommandRegistryError("Invalid command Store contents")
                definitions = {}
                used: set[str] = set()
                for item in items:
                    if not isinstance(item, Mapping):
                        raise CommandRegistryError("Invalid command Store entry")
                    definition = _definition(
                        item.get("name"),
                        item.get("aliases"),
                        item.get("contact_ids"),
                        item.get("allow_current_guests"),
                    )
                    if used.intersection((definition.name, *definition.aliases)):
                        raise CommandRegistryError("Overlapping command names")
                    definitions[definition.name] = definition
                    used.update((definition.name, *definition.aliases))
        except Exception:
            self._commands.clear()
            self.available = False
            self.failure_reason = "command_store_unavailable"
            return
        self._commands = definitions
        self.available = True
        self.failure_reason = None

    def list_commands(self) -> list[dict[str, Any]]:
        """Return non-sensitive declaration metadata for an HA administrator."""
        if not self.available:
            raise CommandRegistryError("Command Store is unavailable")
        return [
            definition.as_storage()
            for definition in sorted(
                self._commands.values(), key=lambda item: item.name
            )
        ]

    async def async_register(
        self,
        name: str,
        aliases: Sequence[str],
        contact_ids: Sequence[str],
        allow_current_guests: bool,
    ) -> None:
        """Atomically create or replace a declaration without running actions."""
        definition = _definition(
            name, list(aliases), list(contact_ids), allow_current_guests
        )
        async with self._lock:
            if not self.available:
                raise CommandRegistryError("Command Store is unavailable")
            candidate = dict(self._commands)
            candidate[definition.name] = definition
            used: set[str] = set()
            for item in candidate.values():
                names = (item.name, *item.aliases)
                if used.intersection(names):
                    raise CommandRegistryError(
                        "Command name or alias is already registered"
                    )
                used.update(names)
            if len(candidate) > MAX_COMMANDS:
                raise CommandRegistryError("Too many commands")
            await self._save(candidate)
            self._commands = candidate

    async def async_unregister(self, name: str) -> None:
        """Remove one canonical command name without affecting other entries."""
        canonical = _name(name)
        async with self._lock:
            if not self.available:
                raise CommandRegistryError("Command Store is unavailable")
            if canonical not in self._commands:
                raise CommandRegistryError("Command is not registered")
            candidate = dict(self._commands)
            del candidate[canonical]
            await self._save(candidate)
            self._commands = candidate

    async def _save(self, candidate: Mapping[str, CommandDefinition]) -> None:
        """Keep in-memory state unchanged when a disk write fails."""
        try:
            await self._store.async_save(
                {
                    "version": STORAGE_VERSION,
                    "commands": [
                        item.as_storage()
                        for item in sorted(
                            candidate.values(), key=lambda item: item.name
                        )
                    ],
                }
            )
        except Exception as err:
            raise CommandRegistryError("Could not save command declarations") from err

    def publish_from_message(
        self, event_data: Mapping[str, Any], *, context: Context | None = None
    ) -> bool:
        """Publish one structured request only for a registered, allowed DM."""
        if not self.available or event_data.get("config_entry_id") != self._entry_id:
            return False
        if (
            event_data.get("type") != "message.received"
            or event_data.get("conversation_type") != "direct"
        ):
            return False
        message = event_data.get("message")
        sender = event_data.get("sender")
        if (
            not isinstance(message, Mapping)
            or message.get("kind") != "text"
            or not isinstance(sender, Mapping)
        ):
            return False
        text = message.get("text")
        if (
            not isinstance(text, str)
            or len(text) > MAX_COMMAND_TEXT
            or "\n" in text
            or "\r" in text
        ):
            return False
        match = _INVOCATION.fullmatch(text.rstrip(" \t"))
        if match is None:
            return False
        invoked_as, argument_text = match.groups()
        invoked_as = invoked_as.lower()
        definition = next(
            (
                item
                for item in self._commands.values()
                if invoked_as in (item.name, *item.aliases)
            ),
            None,
        )
        if definition is None:
            return False
        membership = event_data.get("membership")
        contact_allowed = sender.get("contact_id") in definition.contact_ids
        guest_allowed = (
            definition.allow_current_guests
            and isinstance(membership, Mapping)
            and membership.get("kind") == "guest"
            and membership.get("status") == "active"
            and isinstance(sender.get("participant_id"), str)
        )
        if not contact_allowed and not guest_allowed:
            return False
        try:
            arguments = shlex.split(argument_text or "")
        except ValueError:
            return False
        if len(arguments) > MAX_ARGUMENTS or any(
            len(value) > MAX_ARGUMENT_LENGTH for value in arguments
        ):
            return False
        if not all(
            isinstance(event_data.get(key), str)
            for key in ("event_id", "conversation_id", "occurred_at")
        ) or not isinstance(message.get("id"), str):
            return False
        request = {
            "schema_version": CHANNEL_SCHEMA_VERSION,
            "event_id": event_data["event_id"],
            "type": "command.requested",
            "config_entry_id": self._entry_id,
            "conversation_id": event_data["conversation_id"],
            "conversation_type": "direct",
            "occurred_at": event_data["occurred_at"],
            "sender": deepcopy(dict(sender)),
            "source_message_id": message["id"],
            "command": {
                "name": definition.name,
                "invoked_as": invoked_as,
                "arguments": arguments,
            },
        }
        if guest_allowed and isinstance(membership, Mapping):
            request["membership"] = deepcopy(dict(membership))
            group = event_data.get("group")
            if isinstance(group, Mapping):
                request["group"] = deepcopy(dict(group))
        self._hass.bus.async_fire(EVENT_WAHA_WHATSAPP, request, context=context)
        self.requested_count += 1
        return True
