"""Command declarations recognize trusted DMs but never execute HA actions."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from ._loader import load_integration_module

commands = load_integration_module("commands")


class _Store:
    def __init__(self, data=None) -> None:
        self.data = data
        self.fail_save = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail_save:
            raise OSError("disk failed")
        self.data = deepcopy(data)


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def async_fire(self, event_type, data, *, context=None):
        self.events.append((event_type, data, context))


def _message(text="/status", *, contact_id="contact-a", guest=False, group=False):
    event = {
        "schema_version": 1,
        "event_id": "evt_safe",
        "type": "message.received",
        "config_entry_id": "entry-a",
        "conversation_id": "private_route" if guest else "configured_route",
        "conversation_type": "group" if group else "direct",
        "occurred_at": "2026-09-28T12:00:00+00:00",
        "sender": {"contact_id": contact_id} if contact_id else {},
        "message": {"id": "msg_safe", "kind": "text", "text": text},
    }
    if guest:
        event["sender"]["participant_id"] = "participant_safe"
        event["membership"] = {
            "membership_id": "membership_safe",
            "kind": "guest",
            "status": "active",
        }
        event["group"] = {"group_id": "group_safe", "purpose": "guests"}
    return event


@pytest.mark.asyncio
async def test_register_persist_restore_and_publish_allowed_contact() -> None:
    store = _Store()
    bus = _Bus()
    registry = commands.CommandRegistry(
        SimpleNamespace(bus=bus), "entry-a", store=store
    )
    await registry.async_start()
    await registry.async_register("status", ["house"], ["contact-a"], False)
    assert store.data["commands"][0]["contact_ids"] == ["contact-a"]
    assert registry.publish_from_message(
        _message("/HOUSE 'living room'"), context="ctx"
    )
    assert len(bus.events) == 1
    event_type, request, context = bus.events[0]
    assert event_type == "waha_whatsapp_event"
    assert context == "ctx"
    assert request["type"] == "command.requested"
    assert request["command"] == {
        "name": "status",
        "invoked_as": "house",
        "arguments": ["living room"],
    }
    assert request["event_id"] == "evt_safe"
    assert request["source_message_id"] == "msg_safe"
    assert "phone" not in str(request)

    restored = commands.CommandRegistry(
        SimpleNamespace(bus=_Bus()), "entry-a", store=store
    )
    await restored.async_start()
    assert restored.list_commands() == registry.list_commands()


@pytest.mark.asyncio
async def test_explicit_guest_permission_requires_active_private_membership() -> None:
    bus = _Bus()
    registry = commands.CommandRegistry(
        SimpleNamespace(bus=bus), "entry-a", store=_Store()
    )
    await registry.async_start()
    await registry.async_register("status", [], [], True)
    assert registry.publish_from_message(
        _message("/status", contact_id=None, guest=True)
    )
    request = bus.events[0][1]
    assert request["membership"]["membership_id"] == "membership_safe"
    assert request["group"]["purpose"] == "guests"

    former_guest = _message("/status", contact_id=None, guest=True)
    former_guest["membership"]["status"] = "left"
    unknown_member = _message("/status", contact_id=None, guest=True)
    del unknown_member["sender"]["participant_id"]
    for invalid in (
        _message("/status", contact_id=None),
        _message("/status", contact_id=None, guest=True, group=True),
        former_guest,
        unknown_member,
    ):
        assert not registry.publish_from_message(invalid)
    assert len(bus.events) == 1


@pytest.mark.asyncio
async def test_unknown_unlisted_and_invalid_commands_remain_only_messages() -> None:
    bus = _Bus()
    registry = commands.CommandRegistry(
        SimpleNamespace(bus=bus), "entry-a", store=_Store()
    )
    await registry.async_start()
    await registry.async_register("status", [], ["contact-a"], False)
    wrong_entry = _message()
    wrong_entry["config_entry_id"] = "entry-b"
    media_message = _message()
    media_message["message"]["kind"] = "image"
    for event in (
        _message("/unknown"),
        _message("/status", contact_id="contact-b"),
        wrong_entry,
        media_message,
        _message("status"),
        _message(" /status"),
        _message("/status\nmore"),
        _message("/status 'unclosed"),
        _message("/status " + "x" * 257),
        _message("/status " + "a " * 9),
        _message("/status"),
    ):
        if event["message"]["text"] == "/status":
            event["type"] = "reaction.added"
        assert not registry.publish_from_message(event)
    assert bus.events == []


@pytest.mark.asyncio
async def test_alias_collision_rejected_and_failed_save_does_not_activate() -> None:
    store = _Store()
    registry = commands.CommandRegistry(
        SimpleNamespace(bus=_Bus()), "entry-a", store=store
    )
    await registry.async_start()
    await registry.async_register("status", ["state"], ["contact-a"], False)
    with pytest.raises(commands.CommandRegistryError, match="already registered"):
        await registry.async_register("state", [], ["contact-a"], False)
    with pytest.raises(commands.CommandRegistryError, match="unique"):
        await registry.async_register("help", ["help"], ["contact-a"], False)
    store.fail_save = True
    with pytest.raises(commands.CommandRegistryError, match="Could not save"):
        await registry.async_register("help", [], ["contact-a"], False)
    assert [item["name"] for item in registry.list_commands()] == ["status"]


@pytest.mark.asyncio
async def test_corrupt_store_fails_closed_without_overwriting_it() -> None:
    store = _Store({"version": 1, "commands": [{"name": "status"}]})
    registry = commands.CommandRegistry(
        SimpleNamespace(bus=_Bus()), "entry-a", store=store
    )
    await registry.async_start()
    assert registry.available is False
    assert registry.failure_reason == "command_store_unavailable"
    assert registry.publish_from_message(_message()) is False
    with pytest.raises(commands.CommandRegistryError, match="unavailable"):
        await registry.async_register("status", [], ["contact-a"], False)
    assert store.data["commands"] == [{"name": "status"}]
