"""Managed-group webhook hints never grant membership on their own."""

import time

from tests._loader import load_integration_module

guest_webhook = load_integration_module("guest_webhook")


def _event(*, event="group.v2.participants", group="123-456@g.us"):
    return {
        "id": "evt_example",
        "event": event,
        "session": "default",
        "payload": {
            "type": "join",
            "timestamp": time.time(),
            "group": {"id": group},
            "participants": [
                {"id": "111@lid", "pn": "222@c.us", "role": "participant"}
            ],
        },
    }


def test_participant_hint_parses_exact_group_and_verified_aliases():
    hint = guest_webhook.participant_hint(
        _event(), session="default", group_jid="123-456@g.us"
    )
    assert hint is not None
    assert hint.change_type == "join"
    assert hint.participants == (("111@lid", "222@c.us"),)
    assert hint.occurred_at is not None


def test_other_group_or_session_is_ignored():
    assert (
        guest_webhook.participant_hint(
            _event(), session="other", group_jid="123-456@g.us"
        )
        is None
    )
    assert (
        guest_webhook.participant_hint(
            _event(), session="default", group_jid="999@g.us"
        )
        is None
    )


def test_stale_event_still_triggers_refresh_without_timing_join():
    data = _event()
    data["payload"]["timestamp"] = time.time() - 7200
    hint = guest_webhook.participant_hint(
        data, session="default", group_jid="123-456@g.us"
    )
    assert hint is not None
    assert hint.occurred_at is None


def test_bad_role_or_alias_rejected():
    data = _event()
    data["payload"]["participants"][0]["role"] = "owner"
    assert (
        guest_webhook.participant_hint(
            data, session="default", group_jid="123-456@g.us"
        )
        is None
    )
    data = _event()
    data["payload"]["participants"][0]["pn"] = "../../../@c.us"
    assert (
        guest_webhook.participant_hint(
            data, session="default", group_jid="123-456@g.us"
        )
        is None
    )


def test_join_request_is_not_membership_signal():
    data = _event(event="group.v2.participants.join-request")
    assert (
        guest_webhook.participant_hint(
            data, session="default", group_jid="123-456@g.us"
        )
        is None
    )
    assert not guest_webhook.is_managed_group_signal(
        data, session="default", group_jid="123-456@g.us"
    )


def test_malformed_participant_hint_still_requests_authoritative_refresh():
    """Signed exact-group callbacks can refresh without trusting hint details."""
    data = _event()
    del data["id"]
    assert (
        guest_webhook.participant_hint(
            data, session="default", group_jid="123-456@g.us"
        )
        is None
    )
    assert guest_webhook.is_managed_group_signal(
        data, session="default", group_jid="123-456@g.us"
    )
