"""Managed guest identity is durable and fails closed."""

from __future__ import annotations

from copy import deepcopy
from time import time

import pytest

from ._loader import load_integration_module

module = load_integration_module("guest_registry")
SAFETY = {"security_verified": True, "bot_admin_verified": True}


class FakeStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.fail_save = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail_save:
            raise OSError("disk unavailable")
        self.data = deepcopy(data)


def make_registry(store=None):
    return module.GuestRegistry(None, "entry-a", store=store or FakeStore())


async def setup(store=None):
    registry = make_registry(store)
    await registry.async_initialize_empty()
    await registry.async_start()
    await registry.async_bind_account("393331111111@c.us")
    await registry.async_set_group("120363123456789-1234567890@g.us")
    await registry.async_set_provisioning_status("ready")
    return registry


def member(alias="441234567890@c.us", role="participant"):
    return {"aliases": [alias], "kind": "guest", "role": role}


@pytest.mark.asyncio
async def test_missing_store_is_not_pristine_and_initialization_never_overwrites():
    store = FakeStore()
    registry = make_registry(store)
    await registry.async_start()
    assert not registry.storage_healthy
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await registry.async_bind_account("393331111111@c.us")
    await registry.async_initialize_empty()
    assert registry.storage_healthy
    saved = deepcopy(store.data)
    await registry.async_initialize_empty()
    assert store.data == saved
    store.data = {"version": 1, "participants": {}}
    with pytest.raises(ValueError, match="Invalid registry collections"):
        await registry.async_initialize_empty()
    assert store.data == {"version": 1, "participants": {}}


@pytest.mark.asyncio
async def test_snapshot_restart_and_per_stay_routes():
    start = time() - 500
    store = FakeStore()
    registry = await setup(store)
    assert (
        await registry.async_reconcile(
            [member()],
            observed_at=start,
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
        == []
    )
    first = registry.snapshot()["memberships"][0]
    assert registry.group_matches(
        "120363123456789-1234567890@g.us", "393331111111@c.us"
    )
    assert not registry.group_matches(
        "120363123456789-1234567890@g.us", "393332222222@c.us"
    )
    assert (
        registry.resolve_group_conversation(registry.snapshot()["conversation_id"])
        == "120363123456789-1234567890@g.us"
    )
    assert first["participant_id"].startswith("participant_")
    assert first["membership_id"].startswith("membership_")
    assert first["direct_conversation_id"].startswith("guest_direct_")
    assert (
        registry.resolve_route(first["direct_conversation_id"]) == "441234567890@c.us"
    )
    assert registry.resolve_member("441234567890@c.us", occurred_at=start - 1) is None
    assert (
        registry.resolve_member("441234567890@c.us", occurred_at=start)["membership_id"]
        == first["membership_id"]
    )
    assert "441234567890" not in str(registry.snapshot())

    restarted = make_registry(store)
    await restarted.async_start()
    assert not restarted.snapshot()["ready"]
    assert restarted.resolve_route(first["direct_conversation_id"]) is None
    assert (
        await restarted.async_reconcile(
            [member()],
            observed_at=start + 100,
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
        == []
    )
    assert (
        restarted.snapshot()["memberships"][0]["membership_id"]
        == first["membership_id"]
    )
    changes = await restarted.async_reconcile(
        [], observed_at=start + 200, verified_bot_jid="393331111111@c.us", **SAFETY
    )
    assert [(change.type, change.membership_id) for change in changes] == [
        ("left", first["membership_id"])
    ]
    assert restarted.resolve_route(first["direct_conversation_id"]) is None
    assert (
        restarted.snapshot(membership_id=first["membership_id"])["memberships"][0][
            "status"
        ]
        == "left"
    )
    changes = await restarted.async_reconcile(
        [member()],
        observed_at=start + 300,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    second = next(
        m for m in restarted.snapshot()["memberships"] if m["status"] == "active"
    )
    assert changes[0].type == "joined"
    assert second["participant_id"] == first["participant_id"]
    assert second["membership_id"] != first["membership_id"]
    assert second["direct_conversation_id"] != first["direct_conversation_id"]
    assert (
        restarted.resolve_member("441234567890@c.us", occurred_at=start + 150) is None
    )
    assert (
        restarted.resolve_member("441234567890@c.us", occurred_at=start + 300)[
            "membership_id"
        ]
        == second["membership_id"]
    )


@pytest.mark.asyncio
async def test_account_change_and_create_unknown_fail_closed():
    store = FakeStore()
    registry = make_registry(store)
    await registry.async_initialize_empty()
    await registry.async_start()
    await registry.async_bind_account("393331111111@c.us")
    await registry.async_mark_create_started()
    await registry.async_mark_create_unknown()
    assert registry.snapshot()["provisioning_status"] == "create_unknown"
    with pytest.raises(ValueError, match="must not be retried"):
        await registry.async_mark_create_started()
    with pytest.raises(ValueError, match="confirmed recovery"):
        await registry.async_set_group("120363123456789-1234567890@g.us")
    with pytest.raises(ValueError, match="another bot account"):
        await registry.async_bind_account("393332222222@c.us")
    assert not registry.snapshot()["ready"]


@pytest.mark.asyncio
async def test_explicit_unknown_group_adoption_stays_suspended():
    registry = make_registry()
    await registry.async_initialize_empty()
    await registry.async_start()
    await registry.async_bind_account("393331111111@c.us")
    await registry.async_mark_create_started()
    await registry.async_mark_create_unknown()
    with pytest.raises(ValueError, match="bot account changed"):
        await registry.async_adopt_unknown_group(
            "120363123456789-1234567890@g.us",
            verified_bot_jid="393332222222@c.us",
        )
    assert registry.saved_group_jid is None
    await registry.async_adopt_unknown_group(
        "120363123456789-1234567890@g.us",
        verified_bot_jid="393331111111@c.us",
    )
    assert registry.provisioning_status == "suspended"
    assert not registry.snapshot()["ready"]
    with pytest.raises(ValueError, match="Unknown create outcome"):
        await registry.async_adopt_unknown_group(
            "120363123456789-1234567890@g.us",
            verified_bot_jid="393331111111@c.us",
        )


@pytest.mark.asyncio
async def test_interrupted_create_cannot_be_saved_without_reviewed_adoption():
    store = FakeStore()
    registry = make_registry(store)
    await registry.async_initialize_empty()
    await registry.async_start()
    await registry.async_bind_account("393331111111@c.us")
    await registry.async_mark_create_started()
    restarted = make_registry(store)
    await restarted.async_start()
    with pytest.raises(ValueError, match="confirmed recovery"):
        await restarted.async_set_group("120363123456789-1234567890@g.us")
    assert restarted.saved_group_jid is None
    await restarted.async_adopt_unknown_group(
        "120363123456789-1234567890@g.us",
        verified_bot_jid="393331111111@c.us",
    )
    assert restarted.provisioning_status == "suspended"


@pytest.mark.asyncio
async def test_stale_webhook_hint_does_not_reject_new_roster_or_misattributed_join():
    registry = await setup()
    await registry.async_reconcile(
        [], observed_at=1000, verified_bot_jid="393331111111@c.us", **SAFETY
    )
    changes = await registry.async_reconcile(
        [member()],
        observed_at=1100,
        verified_bot_jid="393331111111@c.us",
        event_hints=[
            {"alias": "441234567890@c.us", "type": "join", "occurred_at": 999}
        ],
        **SAFETY,
    )
    assert len(changes) == 1
    assert changes[0].source == "reconciliation"
    assert changes[0].occurred_at is None
    assert registry.snapshot()["memberships"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_targeted_snapshot_distinguishes_missing_from_left():
    registry = await setup()
    await registry.async_reconcile(
        [member()], observed_at=1000, verified_bot_jid="393331111111@c.us", **SAFETY
    )
    membership_id = registry.snapshot()["memberships"][0]["membership_id"]
    await registry.async_reconcile(
        [], observed_at=1100, verified_bot_jid="393331111111@c.us", **SAFETY
    )
    assert registry.snapshot(membership_id=membership_id)["membership_found"] is True
    assert registry.snapshot(membership_id="missing")["membership_found"] is False


@pytest.mark.asyncio
async def test_conflicting_aliases_and_unverified_mapping_rejected():
    registry = await setup()
    await registry.async_reconcile(
        [member("123456@lid"), member("441234567890@c.us")],
        observed_at=1000,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    with pytest.raises(ValueError, match="Conflicting verified aliases"):
        await registry.async_reconcile(
            [
                {
                    "aliases": ["123456@lid", "441234567890@c.us"],
                    "aliases_verified": True,
                    "kind": "guest",
                    "role": "participant",
                }
            ],
            observed_at=1100,
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
    with pytest.raises(ValueError, match="Alias mapping is not verified"):
        await registry.async_reconcile(
            [
                {
                    "aliases": ["123456@lid", "441234567890@c.us"],
                    "kind": "guest",
                    "role": "participant",
                }
            ],
            observed_at=1100,
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
    assert registry.snapshot()["revision"] == 1


@pytest.mark.asyncio
async def test_corrupt_store_and_save_failure_disable_routing():
    corrupt = make_registry(FakeStore({"version": 1, "participants": {}}))
    await corrupt.async_start()
    assert not corrupt.storage_healthy
    assert not corrupt.snapshot()["ready"]
    with pytest.raises(RuntimeError):
        await corrupt.async_bind_account("393331111111@c.us")

    store = FakeStore()
    registry = await setup(store)
    await registry.async_reconcile(
        [member()], observed_at=1000, verified_bot_jid="393331111111@c.us", **SAFETY
    )
    route = registry.snapshot()["memberships"][0]["direct_conversation_id"]
    store.fail_save = True
    with pytest.raises(OSError, match="disk unavailable"):
        await registry.async_reconcile(
            [], observed_at=1100, verified_bot_jid="393331111111@c.us", **SAFETY
        )
    assert registry.resolve_route(route) is None
    assert not registry.snapshot()["ready"]


@pytest.mark.asyncio
async def test_send_requires_live_verification_and_excludes_bot():
    registry = await setup()
    await registry.async_reconcile(
        [member("393331111111@c.us"), member()],
        observed_at=1000,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    assert len(registry.snapshot()["memberships"]) == 1
    route = registry.snapshot()["memberships"][0]["direct_conversation_id"]
    verified = []
    sent = []

    async def verify(destination):
        verified.append(destination)
        return True

    async def send(destination):
        sent.append(destination)
        return "ok"

    assert await registry.async_with_route(route, verify, send) == "ok"
    assert verified == sent == ["441234567890@c.us"]
    registry.suspend()
    with pytest.raises(ValueError, match="Guest route is no longer verified"):
        await registry.async_with_route(route, verify, send)


@pytest.mark.asyncio
async def test_verified_security_role_time_and_webhook_hints():
    start = time() - 200
    registry = await setup()
    with pytest.raises(ValueError, match="security, or bot role"):
        await registry.async_reconcile(
            [member()], observed_at=start, verified_bot_jid="393331111111@c.us"
        )
    with pytest.raises(ValueError, match="Invalid timestamp"):
        await registry.async_reconcile(
            [member()],
            observed_at=float("nan"),
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
    with pytest.raises(ValueError, match="Invalid roster member"):
        await registry.async_reconcile(
            [member(role="unknown")],
            observed_at=start,
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
    await registry.async_reconcile(
        [member()],
        observed_at=start,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    # A mismatched join hint cannot relabel a leave as a webhook event.
    changes = await registry.async_reconcile(
        [],
        observed_at=start + 100,
        verified_bot_jid="393331111111@c.us",
        event_hints=[
            {"alias": "441234567890@c.us", "type": "join", "occurred_at": start + 50}
        ],
        **SAFETY,
    )
    assert changes[0].source == "reconciliation"
    assert changes[0].occurred_at is None
    changes = await registry.async_reconcile(
        [member()],
        observed_at=start + 150,
        verified_bot_jid="393331111111@c.us",
        event_hints=[
            {"alias": "441234567890@c.us", "type": "join", "occurred_at": start + 125}
        ],
        **SAFETY,
    )
    assert changes[0].source == "webhook"
    assert changes[0].occurred_at == start + 125
    assert registry.resolve_member("441234567890@c.us", occurred_at=start + 120) is None


@pytest.mark.asyncio
async def test_closed_tombstone_expiry_prunes_orphan_aliases():
    registry = await setup()
    start = time() - module.CLOSED_RETENTION_SECONDS - 1000
    await registry.async_reconcile(
        [member()],
        observed_at=start,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    old_id = registry.snapshot()["memberships"][0]["participant_id"]
    await registry.async_reconcile(
        [],
        observed_at=start + 1,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    await registry.async_reconcile(
        [],
        observed_at=time() - 2,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    assert not registry.snapshot()["memberships"]
    assert not registry._data["participants"]
    await registry.async_reconcile(
        [member()],
        observed_at=time() - 1,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    assert registry.snapshot()["memberships"][0]["participant_id"] != old_id


@pytest.mark.asyncio
async def test_invalid_roster_suspends_and_public_id_corruption_fails_closed():
    store = FakeStore()
    registry = await setup(store)
    await registry.async_reconcile(
        [member()],
        observed_at=time() - 10,
        verified_bot_jid="393331111111@c.us",
        **SAFETY,
    )
    route = registry.snapshot()["memberships"][0]["direct_conversation_id"]
    assert registry.saved_group_jid == "120363123456789-1234567890@g.us"
    with pytest.raises(ValueError, match="Invalid roster member"):
        await registry.async_reconcile(
            [member(role="invalid")],
            observed_at=time() - 1,
            verified_bot_jid="393331111111@c.us",
            **SAFETY,
        )
    assert registry.resolve_route(route) is None
    poisoned = deepcopy(store.data)
    poisoned["group"]["conversation_id"] = "393331111111@c.us"
    corrupt = make_registry(FakeStore(poisoned))
    await corrupt.async_start()
    assert not corrupt.storage_healthy
    assert corrupt.snapshot()["conversation_id"] is None
