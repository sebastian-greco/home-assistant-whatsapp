"""Authenticated WAHA poll webhooks and Home Assistant action events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import EVENT_MOBILE_APP_NOTIFICATION_ACTION
from .polls import PollOption, PollRegistry, parse_poll_vote, verify_hmac_sha512

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class WahaPollManager:
    """Persist outgoing polls and translate stable votes to HA events."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session_name: str,
        webhook_secret: str,
    ) -> None:
        """Initialize one config entry's actionable poll manager."""
        self._hass = hass
        self._entry = entry
        self._session_name = session_name
        self._webhook_secret = webhook_secret
        self._registry = PollRegistry()
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"waha_whatsapp.polls.{entry.entry_id}",
            private=True,
        )
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self.failed_vote_count = 0

    @property
    def pending_count(self) -> int:
        """Return the number of outbound polls still awaiting a stable choice."""
        return len(self._registry.pending_polls())

    async def async_start(self) -> None:
        """Restore pending polls and their vote-settling timers."""
        self._registry.load(await self._store.async_load())
        for pending in self._registry.pending_polls():
            if (
                pending.commit_at is not None
                and pending.latest_vote_timestamp is not None
                and pending.selected_title is not None
            ):
                self._schedule_commit(
                    pending.message_id,
                    pending.latest_vote_timestamp,
                    pending.commit_at,
                )

    def stop(self) -> None:
        """Cancel timers when the config entry unloads."""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

    async def async_register_poll(
        self,
        message_id: str,
        chat_id: str,
        options: tuple[PollOption, ...],
        settle_seconds: float,
    ) -> None:
        """Persist the correlation data for one outbound actionable poll."""
        self._registry.register(message_id, chat_id, options, settle_seconds)
        await self._async_save()

    def verify_signature(self, raw_body: bytes, signature: str | None) -> bool:
        """Verify WAHA's SHA-512 HMAC over the unmodified request body."""
        return verify_hmac_sha512(self._webhook_secret, raw_body, signature)

    async def async_handle_payload(self, data: Mapping[str, Any]) -> None:
        """Process one authenticated WAHA poll event."""
        event = data.get("event")
        if event == "poll.vote.failed":
            if data.get("session") == self._session_name:
                self.failed_vote_count += 1
                _LOGGER.warning(
                    "WAHA could not decrypt a poll vote; no Home Assistant "
                    "action was fired"
                )
            return

        vote = parse_poll_vote(data, self._session_name)
        if vote is None or not self._registry.apply_vote(vote, time.time()):
            return

        previous = self._timers.pop(vote.message_id, None)
        if previous is not None:
            previous.cancel()

        pending = self._registry.pending(vote.message_id)
        if pending is not None and pending.commit_at is not None:
            self._schedule_commit(vote.message_id, vote.timestamp, pending.commit_at)
        await self._async_save()

    @staticmethod
    def decode_payload(raw_body: bytes) -> Mapping[str, Any] | None:
        """Decode one JSON object without accepting other JSON top-level types."""
        try:
            data = json.loads(raw_body)
        except UnicodeDecodeError, json.JSONDecodeError:
            return None
        return data if isinstance(data, Mapping) else None

    def _schedule_commit(
        self, message_id: str, vote_timestamp: float, commit_at: float
    ) -> None:
        """Schedule one vote after its correction window closes."""
        previous = self._timers.pop(message_id, None)
        if previous is not None:
            previous.cancel()
        self._timers[message_id] = self._hass.loop.call_later(
            max(0.0, commit_at - time.time()),
            self._create_commit_task,
            message_id,
            vote_timestamp,
        )

    def _create_commit_task(self, message_id: str, vote_timestamp: float) -> None:
        """Move the timer callback into an async Home Assistant task."""
        self._timers.pop(message_id, None)
        self._entry.async_create_task(
            self._hass,
            self._async_commit(message_id, vote_timestamp),
            "settle WAHA poll action",
        )

    async def _async_commit(self, message_id: str, vote_timestamp: float) -> None:
        """Fire the legacy-compatible event once for the settled selection."""
        committed, action = self._registry.commit(
            message_id, vote_timestamp, time.time()
        )
        if not committed:
            pending = self._registry.pending(message_id)
            if (
                pending is not None
                and pending.latest_vote_timestamp == vote_timestamp
                and pending.commit_at is not None
            ):
                self._schedule_commit(message_id, vote_timestamp, pending.commit_at)
            return

        await self._async_save()
        if action is not None:
            self._hass.bus.async_fire(
                EVENT_MOBILE_APP_NOTIFICATION_ACTION,
                {"action": action},
            )

    async def _async_save(self) -> None:
        """Write correlation state immediately so restarts do not lose votes."""
        await self._store.async_save(self._registry.as_dict())
