"""Exercise the real inbound manager and registry together without HA installed."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from ._loader import load_integration_module


class _Context:
    def __init__(self, *, user_id: str) -> None:
        self.user_id = user_id


_core_stub = ModuleType("homeassistant.core")
_core_stub.Context = _Context
_previous_core = sys.modules.get("homeassistant.core")
sys.modules["homeassistant.core"] = _core_stub
try:
    inbound = load_integration_module("inbound")
finally:
    if _previous_core is None:
        del sys.modules["homeassistant.core"]
    else:
        sys.modules["homeassistant.core"] = _previous_core

registry_module = load_integration_module("channel_registry")
commands_module = load_integration_module("commands")


class _Store:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


class _Bus:
    def __init__(self, *, mutate_message=False) -> None:
        self.events = []
        self.mutate_message = mutate_message

    def async_fire(self, event_type, data, *, context=None):
        self.events.append((event_type, data, context))
        if self.mutate_message and data["type"] == "message.received":
            data["sender"]["contact_id"] = "attacker"
            data["message"]["text"] = "/other"


@pytest.mark.asyncio
async def test_authenticated_direct_command_is_derived_after_message_dedup(
    monkeypatch,
) -> None:
    """The command parser receives only the accepted inbound-manager output."""
    monkeypatch.setattr(inbound.time, "time", lambda: 1_755_776_096)
    monkeypatch.setattr(
        registry_module,
        "_notify_entity_id",
        lambda _hass, _entry_id, _subentry_id: "notify.waha_seba",
    )
    bus = _Bus(mutate_message=True)
    hass = SimpleNamespace(bus=bus, states=SimpleNamespace(get=lambda _id: None))
    entry = SimpleNamespace(
        entry_id="entry-one",
        data={"session": "default", "webhook_secret": "stable-private-key"},
        subentries={
            "seba": SimpleNamespace(
                subentry_id="seba",
                subentry_type="recipient",
                data={"recipient": "393331234567"},
            )
        },
    )
    client = SimpleNamespace(session_name="default")
    channel = registry_module.ChannelRegistry(hass, entry, client, store=_Store())
    await channel.async_start()
    declarations = commands_module.CommandRegistry(hass, "entry-one", store=_Store())
    await declarations.async_start()
    await declarations.async_register("status", [], ["seba"], False)
    manager = inbound.WahaInboundManager(
        hass, entry, channel, command_registry=declarations
    )
    message = {
        "id": "evt-command",
        "event": "message",
        "session": "default",
        "payload": {
            "id": "false_393331234567@c.us_CMD",
            "timestamp": 1_755_776_096,
            "from": "393331234567@c.us",
            "fromMe": False,
            "body": "/status",
            "hasMedia": False,
        },
    }

    await manager.async_handle_payload(message)
    await manager.async_handle_payload(message)

    assert [item[1]["type"] for item in bus.events] == [
        "message.received",
        "command.requested",
    ]
    assert bus.events[1][1]["command"]["name"] == "status"
    assert bus.events[1][1]["sender"]["contact_id"] == "seba"
    assert declarations.requested_count == 1


@pytest.mark.asyncio
async def test_command_parser_failure_does_not_break_existing_message_channel(
    monkeypatch,
) -> None:
    """A command feature bug must not turn an accepted webhook into HTTP 500."""
    monkeypatch.setattr(inbound.time, "time", lambda: 1_755_776_096)
    monkeypatch.setattr(
        registry_module,
        "_notify_entity_id",
        lambda _hass, _entry_id, _subentry_id: "notify.waha_seba",
    )
    bus = _Bus()
    hass = SimpleNamespace(bus=bus, states=SimpleNamespace(get=lambda _id: None))
    entry = SimpleNamespace(
        entry_id="entry-one",
        data={"session": "default", "webhook_secret": "stable-private-key"},
        subentries={
            "seba": SimpleNamespace(
                subentry_id="seba",
                subentry_type="recipient",
                data={"recipient": "393331234567"},
            )
        },
    )
    channel = registry_module.ChannelRegistry(
        hass, entry, SimpleNamespace(session_name="default"), store=_Store()
    )
    await channel.async_start()

    def fail(_event_data, *, context=None):
        raise RuntimeError("broken command parser")

    manager = inbound.WahaInboundManager(
        hass,
        entry,
        channel,
        command_registry=SimpleNamespace(publish_from_message=fail),
    )
    await manager.async_handle_payload(
        {
            "id": "evt-one",
            "event": "message",
            "session": "default",
            "payload": {
                "id": "false_393331234567@c.us_CMD",
                "timestamp": 1_755_776_096,
                "from": "393331234567@c.us",
                "fromMe": False,
                "body": "/status",
                "hasMedia": False,
            },
        }
    )
    assert [item[1]["type"] for item in bus.events] == ["message.received"]
    assert manager.accepted_count == 1


@pytest.mark.asyncio
async def test_real_registry_routes_text_and_correlated_reaction(monkeypatch) -> None:
    """A configured contact can publish events with IDs usable for replies."""
    monkeypatch.setattr(inbound.time, "time", lambda: 1_755_776_096)
    monkeypatch.setattr(
        registry_module,
        "_notify_entity_id",
        lambda _hass, _entry_id, _subentry_id: "notify.waha_seba",
    )
    bus = _Bus()
    hass = SimpleNamespace(bus=bus, states=SimpleNamespace(get=lambda _id: None))
    entry = SimpleNamespace(
        entry_id="entry-one",
        data={"session": "default", "webhook_secret": "stable-private-key"},
        subentries={
            "seba": SimpleNamespace(
                subentry_id="seba",
                subentry_type="recipient",
                data={"recipient": "393331234567"},
            )
        },
    )
    client = SimpleNamespace(session_name="default")
    registry = registry_module.ChannelRegistry(hass, entry, client, store=_Store())
    await registry.async_start()
    manager = inbound.WahaInboundManager(hass, entry, registry)
    message = {
        "id": "evt-one",
        "event": "message",
        "session": "default",
        "payload": {
            "id": "false_393331234567@c.us_ABC123",
            "timestamp": 1_755_776_096,
            "from": "393331234567@c.us",
            "fromMe": False,
            "body": "ping",
            "hasMedia": False,
        },
    }
    await manager.async_handle_payload(message)
    await manager.async_handle_payload(message)

    assert len(bus.events) == 1
    _, event, _ = bus.events[0]
    assert event["sender"]["notify_entity_id"] == "notify.waha_seba"
    assert registry.resolve_conversation(event["conversation_id"]).recipient == (
        "393331234567"
    )
    assert (
        registry.resolve_message(event["message"]["id"], event["conversation_id"])
        == "false_393331234567@c.us_ABC123"
    )

    await manager.async_handle_payload(
        {
            "id": "evt-two",
            "event": "message.reaction",
            "session": "default",
            "payload": {
                "id": "reaction-1",
                "timestamp": 1_755_776_097,
                "from": "393331234567@c.us",
                "fromMe": False,
                "reaction": {
                    "text": "👍",
                    "messageId": "true_393331234567@c.us_ABC123",
                },
            },
        }
    )

    assert len(bus.events) == 2
    assert bus.events[1][1]["type"] == "reaction.added"
    assert bus.events[1][1]["reaction"]["target_known"] is True
    assert bus.events[1][1]["reaction"]["target_message_id"] == event["message"]["id"]
