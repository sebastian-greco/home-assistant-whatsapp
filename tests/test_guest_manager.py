"""Opt-in managed-group provisioning and drift handling."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from time import time
from types import SimpleNamespace

import pytest

from ._loader import load_integration_module

manager_module = load_integration_module("guest_manager")
registry_module = load_integration_module("guest_registry")
admins_module = load_integration_module("guest_admins")

BOT = "393331111111@c.us"
HOST = "393332222222@c.us"
GUEST = "441234567890@c.us"
GROUP = "120363123456789-1234567890@g.us"


class FakeStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class FakeClient:
    def __init__(self):
        self.session_name = "house"
        self.server = SimpleNamespace(version="2026.8.2", engine="GOWS")
        self.session = SimpleNamespace(status="WORKING", account_id=BOT)
        self.group_id = None
        self.roster = [SimpleNamespace(id=BOT, pn=None, role="admin")]
        self.security = dict(manager_module.SECURE_SETTINGS)
        self.writes = []
        self.create_error = False

    async def async_get_server(self):
        return self.server

    async def async_get_session(self):
        return self.session

    async def async_create_group(self, name, participants):
        self.writes.append(("create", name, tuple(participants)))
        if self.create_error:
            raise TimeoutError("unknown outcome")
        self.group_id = GROUP
        self.roster.extend(
            SimpleNamespace(id=chat, pn=None, role="participant")
            for chat in participants
        )
        return GROUP

    async def async_get_group(self, group_id):
        if group_id != self.group_id:
            raise ValueError("wrong group")
        return {"id": group_id}

    async def async_get_group_participants(self, group_id):
        await self.async_get_group(group_id)
        return deepcopy(self.roster)

    async def async_add_group_participants(self, group_id, participants):
        self.writes.append(("add", tuple(participants)))
        self.roster.extend(
            SimpleNamespace(id=chat, pn=None, role="participant")
            for chat in participants
        )

    async def async_promote_group_admins(self, group_id, participants):
        self.writes.append(("promote", tuple(participants)))
        for item in self.roster:
            if item.id in participants:
                item.role = "admin"

    async def async_set_group_security(self, group_id, **settings):
        self.writes.append(("security",))
        self.security = dict(settings)

    async def async_get_group_security(self, group_id):
        return dict(self.security)

    async def async_send_group_text(self, group_id, text):
        self.writes.append(("send_group", group_id, text))
        return SimpleNamespace(id="message-id", chat_id=group_id)


def make_preview():
    contact = admins_module.GuestAdminContact(
        "admin-user", "Admin", "person.admin", "393332222222", HOST
    )
    return admins_module.GuestAdminPreview((contact,), ())


def make_entry(preview, *, enabled=True, fingerprint=True):
    secret = "private-test-secret"
    options = {
        "guest_group_enabled": enabled,
        "guest_group_name": "Test Guests",
        "guest_group_excluded_admin_user_ids": [],
    }
    if fingerprint:
        options["guest_group_reviewed_admin_fingerprint"] = (
            admins_module.reviewed_admin_fingerprint(preview, [], secret)
        )
    return SimpleNamespace(
        entry_id="entry-a", data={"webhook_secret": secret}, options=options
    )


def make_manager(client=None, store=None, *, enabled=True, fingerprint=True):
    preview = make_preview()
    entry = make_entry(preview, enabled=enabled, fingerprint=fingerprint)
    client = client or FakeClient()
    if store is None:
        store = FakeStore()
        store.data = registry_module.GuestRegistry._empty()
    registry = registry_module.GuestRegistry(None, entry.entry_id, store=store)
    events = []

    async def preview_provider(_hass, _entry):
        return preview

    manager = manager_module.GuestGroupManager(
        None,
        entry,
        client,
        registry,
        preview_provider=preview_provider,
        publish=events.append,
    )
    return manager, client, store, events


async def first_enable(manager):
    """Model the durable Store write made by final options confirmation."""
    await manager.registry.async_initialize_empty()


@pytest.mark.asyncio
async def test_opt_in_create_persist_and_restart_is_read_only():
    manager, client, store, events = make_manager()
    await first_enable(manager)
    result = await manager.async_setup()
    assert result["ready"]
    assert result["group_id"].startswith("group_")
    assert [write[0] for write in client.writes] == ["create", "promote", "security"]
    assert store.data["group"]["jid"] == GROUP
    assert store.data["provisioning_status"] == "ready"
    assert len(result["memberships"]) == 1  # Bot excluded.
    assert result["memberships"][0]["kind"] == "host"
    assert len(events) == 1
    assert events[0]["type"] == "group.membership.reconciled"
    assert events[0]["occurred_at"] is None
    assert "membership" not in events[0]
    assert HOST not in str(events[0])
    assert HOST not in str(result)

    restarted, _, _, _ = make_manager(client, store)
    assert (await restarted.async_setup())["ready"]
    assert [write[0] for write in client.writes] == ["create", "promote", "security"]


@pytest.mark.asyncio
async def test_lost_registry_blocks_restart_and_disabled_reenable():
    manager, client, store, _ = make_manager()
    assert (await manager.async_setup())["ready"]
    assert [write[0] for write in client.writes].count("create") == 1
    store.data = None  # Store file lost after a real group was created.
    restarted, _, _, _ = make_manager(client, store)
    result = await restarted.async_setup()
    assert result["failure_reason"] == "guest_registry_unavailable"
    assert not result["ready"]
    assert [write[0] for write in client.writes].count("create") == 1
    assert store.data is None

    restarted._entry.options["guest_group_enabled"] = False
    assert (await restarted.async_setup())["failure_reason"] == "disabled"
    restarted._entry.options["guest_group_enabled"] = True
    assert (await restarted.async_setup())["failure_reason"] == (
        "guest_registry_unavailable"
    )
    assert [write[0] for write in client.writes].count("create") == 1


@pytest.mark.asyncio
async def test_unreviewed_admin_suspends_reconcile_and_send():
    manager, client, _store, _ = make_manager()
    assert (await manager.async_setup())["ready"]
    client.roster.append(SimpleNamespace(id=GUEST, pn=None, role="admin"))
    assert not await manager.async_verify_group_destination(GROUP)
    assert manager.failure_reason == "unexpected_group_admin"
    result = await manager.async_reconcile(force=True)
    assert not result["ready"]
    assert result["failure_reason"] == "unexpected_group_admin"
    assert [write[0] for write in client.writes].count("create") == 1


@pytest.mark.asyncio
async def test_no_write_without_opt_in_capability_or_review():
    disabled, client, _, _ = make_manager(enabled=False)
    assert not (await disabled.async_setup())["ready"]
    assert not client.writes
    unsupported, client, _, _ = make_manager()
    await first_enable(unsupported)
    client.server.version = "2026.8.1"
    assert (await unsupported.async_setup())[
        "failure_reason"
    ] == "unsupported_waha_capabilities"
    assert not client.writes
    unreviewed, client, _, _ = make_manager(fingerprint=False)
    await first_enable(unreviewed)
    assert (await unreviewed.async_setup())["failure_reason"] == "admin_mapping_changed"
    assert not client.writes


@pytest.mark.asyncio
async def test_read_only_refresh_does_not_strand_incomplete_provisioning():
    """A failed security write remains resumable after queries/periodic reads."""
    manager, client, store, _events = make_manager()
    original = client.async_set_group_security

    async def fail_security(*_args, **_kwargs):
        raise RuntimeError("temporary provider failure")

    client.async_set_group_security = fail_security
    assert not (await manager.async_setup())["ready"]
    assert store.data["provisioning_status"] == "group_saved"
    assert not (await manager.async_reconcile(force=True))["ready"]
    assert store.data["provisioning_status"] == "group_saved"

    client.async_set_group_security = original
    restarted, _, _, _ = make_manager(client, store)
    assert (await restarted.async_setup())["ready"]
    assert [write[0] for write in client.writes].count("create") == 1


@pytest.mark.asyncio
async def test_unknown_create_outcome_blocks_retry():
    manager, client, store, _ = make_manager()
    client.create_error = True
    assert (await manager.async_setup())[
        "failure_reason"
    ] == "group_create_outcome_unknown"
    assert store.data["provisioning_status"] == "create_unknown"
    client.create_error = False
    restarted, _, _, _ = make_manager(client, store)
    assert (await restarted.async_setup())[
        "failure_reason"
    ] == "group_create_outcome_unknown"
    assert [write[0] for write in client.writes] == ["create"]


@pytest.mark.asyncio
async def test_unknown_outcome_requires_exact_read_only_review_and_adoption():
    manager, client, store, _ = make_manager()
    client.create_error = True
    await manager.async_setup()
    client.create_error = False
    client.group_id = GROUP
    client.roster.append(SimpleNamespace(id=HOST, pn=None, role="admin"))
    before = list(client.writes)
    with pytest.raises(
        manager_module.GuestGroupError, match="recovery_group_unverified"
    ):
        await manager.async_review_unknown_group("120363999999999@g.us")
    assert store.data["provisioning_status"] == "create_unknown"
    reviewed = await manager.async_review_unknown_group(GROUP)
    assert reviewed == {"group_id": GROUP, "bot_account": BOT}
    assert client.writes == before
    client.security["members_can_add"] = True
    with pytest.raises(manager_module.GuestGroupError, match="unsafe_group_settings"):
        await manager.async_adopt_unknown_group(
            GROUP, reviewed_bot_account=reviewed["bot_account"]
        )
    assert store.data["group"] is None
    client.security["members_can_add"] = False
    with pytest.raises(manager_module.GuestGroupError, match="bot_account_changed"):
        await manager.async_adopt_unknown_group(
            GROUP, reviewed_bot_account="393339999999@c.us"
        )
    assert store.data["group"] is None
    adopted = await manager.async_adopt_unknown_group(GROUP, reviewed_bot_account=BOT)
    assert adopted["provisioning_status"] == "suspended"
    assert not adopted["ready"]
    assert client.writes == before
    assert (await manager.async_setup())["ready"]
    assert client.writes == before


@pytest.mark.asyncio
async def test_interrupted_creating_state_uses_reviewed_recovery_without_retry():
    manager, client, store, _ = make_manager()
    await manager.registry.async_start()
    await manager.registry.async_bind_account(BOT)
    await manager.registry.async_mark_create_started()
    client.group_id = GROUP
    client.roster.append(SimpleNamespace(id=HOST, pn=None, role="admin"))
    assert (await manager.async_setup())["failure_reason"] == (
        "group_create_outcome_unknown"
    )
    assert store.data["provisioning_status"] == "creating"
    assert client.writes == []
    reviewed = await manager.async_review_unknown_group(GROUP)
    await manager.async_adopt_unknown_group(
        GROUP, reviewed_bot_account=reviewed["bot_account"]
    )
    assert store.data["provisioning_status"] == "suspended"
    assert (await manager.async_setup())["ready"]
    assert client.writes == []


@pytest.mark.asyncio
async def test_failed_recovery_security_or_account_stays_unknown():
    manager, client, store, _ = make_manager()
    client.create_error = True
    await manager.async_setup()
    client.group_id = GROUP
    client.roster.append(SimpleNamespace(id=HOST, pn=None, role="admin"))
    client.security["members_can_add"] = True
    with pytest.raises(manager_module.GuestGroupError, match="unsafe_group_settings"):
        await manager.async_review_unknown_group(GROUP)
    client.security["members_can_add"] = False
    client.session.account_id = "393339999999@c.us"
    with pytest.raises(manager_module.GuestGroupError, match="bot_account_changed"):
        await manager.async_review_unknown_group(GROUP)
    assert store.data["provisioning_status"] == "create_unknown"
    assert store.data["group"] is None


@pytest.mark.asyncio
async def test_missing_guest_webhook_cannot_reenable_group():
    manager, client, _, _ = make_manager()
    await manager.async_setup()
    manager.disable_events("guest_webhook_unavailable")
    assert not (await manager.async_reconcile(force=True))["ready"]
    assert manager.snapshot()["failure_reason"] == "guest_webhook_unavailable"


@pytest.mark.asyncio
async def test_drift_suspends_without_silent_external_repair():
    manager, client, store, _ = make_manager()
    await manager.async_setup()
    before = list(client.writes)
    client.security["members_can_add"] = True
    result = await manager.async_reconcile(force=True)
    assert not result["ready"]
    assert result["failure_reason"] == "unsafe_group_settings"
    assert client.writes == before
    assert store.data["provisioning_status"] == "suspended"
    client.security["members_can_add"] = False
    assert (await manager.async_reconcile(force=True))["ready"]
    assert client.writes == before


@pytest.mark.asyncio
async def test_confirmed_join_event_has_opaque_ids_and_webhook_time():
    manager, client, _, events = make_manager()
    await manager.async_setup()
    events.clear()
    client.roster.append(SimpleNamespace(id=GUEST, pn=None, role="participant"))
    occurred_at = time()
    result = await manager.async_reconcile(
        force=True,
        event_hints=[{"alias": GUEST, "type": "join", "occurred_at": occurred_at}],
    )
    assert result["ready"]
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "group.participant.joined"
    assert event["source"] == "webhook"
    assert event["occurred_at"] is not None
    assert event["participant"]["participant_id"].startswith("participant_")
    assert GUEST not in str(event)


@pytest.mark.asyncio
async def test_duplicate_phone_aliases_in_webhook_do_not_suspend_roster():
    """GOWS may put the same PN in id and pn for one changed member."""
    manager, client, _, events = make_manager()
    await manager.async_setup()
    events.clear()
    client.roster.append(SimpleNamespace(id=GUEST, pn=None, role="participant"))
    handled = await manager.async_handle_webhook(
        {
            "id": "group-event-1",
            "event": "group.v2.participants",
            "session": client.session_name,
            "payload": {
                "type": "join",
                "timestamp": time(),
                "group": {"id": GROUP},
                "participants": [{"id": GUEST, "pn": GUEST, "role": "participant"}],
            },
        }
    )
    assert handled is True
    assert manager.snapshot()["ready"]
    assert len(events) == 1
    assert events[0]["type"] == "group.participant.joined"


@pytest.mark.asyncio
async def test_fresh_send_guards_reject_removed_member_and_unsafe_group():
    manager, client, _, _ = make_manager()
    await manager.async_setup()
    client.roster.append(SimpleNamespace(id=GUEST, pn=None, role="participant"))
    await manager.async_reconcile(force=True)
    assert await manager.async_verify_guest_destination(GUEST)
    assert await manager.async_verify_group_destination(GROUP)
    assert not await manager.async_verify_group_destination("12345@g.us")
    client.roster[-1].role = "left"
    assert not await manager.async_verify_guest_destination(GUEST)
    client.security["members_can_add"] = True
    assert not await manager.async_verify_group_destination(GROUP)


@pytest.mark.asyncio
async def test_group_send_requires_fresh_safe_state():
    manager, client, _, _ = make_manager()
    await manager.async_setup()
    result = await manager.async_send_to_group("Hello")
    assert result.chat_id == GROUP
    assert client.writes[-1] == ("send_group", GROUP, "Hello")
    client.security["members_can_add"] = True
    with pytest.raises(manager_module.GuestGroupError, match="group_send_unavailable"):
        await manager.async_send_to_group("Unsafe")
    assert client.writes[-1] == ("send_group", GROUP, "Hello")


@pytest.mark.asyncio
async def test_webhook_reconcile_waits_for_provisioning():
    manager, client, store, _ = make_manager()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_create = client.async_create_group

    async def delayed_create(name, participants):
        entered.set()
        await release.wait()
        return await original_create(name, participants)

    client.async_create_group = delayed_create
    setup_task = asyncio.create_task(manager.async_setup())
    await entered.wait()
    assert store.data["provisioning_status"] == "creating"
    reconcile_task = asyncio.create_task(manager.async_reconcile(force=True))
    await asyncio.sleep(0)
    assert not reconcile_task.done()
    release.set()
    assert (await setup_task)["ready"]
    assert (await reconcile_task)["ready"]
    assert store.data["provisioning_status"] == "ready"


@pytest.mark.asyncio
async def test_pruned_batch_closures_publish_reconcile_signal():
    manager, client, _, events = make_manager()
    await manager.async_setup()
    guests = [
        SimpleNamespace(id=f"4412345{i:06d}@c.us", pn=None, role="participant")
        for i in range(registry_module.MAX_CLOSED_MEMBERSHIPS + 1)
    ]
    client.roster.extend(guests)
    await manager.async_reconcile(force=True)
    events.clear()
    client.roster = [item for item in client.roster if item not in guests]
    result = await manager.async_reconcile(force=True)
    assert result["ready"]
    assert any(event["type"] == "group.membership.reconciled" for event in events)
    assert len(result["memberships"]) == 1 + registry_module.MAX_CLOSED_MEMBERSHIPS


@pytest.mark.asyncio
async def test_webhook_signal_requires_exact_group_and_ignores_join_requests():
    manager, client, _, events = make_manager()
    await manager.async_setup()
    events.clear()
    client.roster.append(SimpleNamespace(id=GUEST, pn=None, role="participant"))
    callback = {
        "event": "group.v2.participants",
        "session": "house",
        "id": "webhook-1",
        "payload": {
            "group": {"id": GROUP},
            "type": "join",
            "timestamp": time(),
            "participants": [{"id": GUEST, "role": "participant"}],
        },
    }
    assert await manager.async_handle_webhook(callback)
    assert events[0]["source"] == "webhook"
    assert GUEST not in str(events[0])
    callback["event"] = "group.v2.participants.join-request"
    assert not await manager.async_handle_webhook(callback)
    callback["event"] = "group.v2.participants"
    callback["payload"]["group"]["id"] = "111111111@g.us"
    assert not await manager.async_handle_webhook(callback)
