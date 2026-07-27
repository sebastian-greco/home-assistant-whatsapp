"""Tests for actionable poll validation, correlation, and settling."""

import hashlib
import hmac

import pytest

from ._loader import load_integration_module

polls = load_integration_module("polls")


def test_waha_hmac_verification_uses_raw_body_and_sha512() -> None:
    """Only the exact body signed with the private key is authenticated."""
    body = b'{"event":"poll.vote"}'
    signature = hmac.new(b"private-key", body, hashlib.sha512).hexdigest()

    assert polls.verify_hmac_sha512("private-key", body, signature)
    assert not polls.verify_hmac_sha512("private-key", body + b" ", signature)
    assert not polls.verify_hmac_sha512("other-key", body, signature)
    assert not polls.verify_hmac_sha512("private-key", body, None)


def vote_payload(
    *,
    selected_options: list[str] | None = None,
    timestamp: float = 100,
    sender: str = "393331234567@c.us",
    poll_chat_id: str = "393331234567@c.us",
    message_id: str = "poll-1",
    session: str = "house",
) -> dict:
    """Build the relevant subset of a WAHA poll.vote webhook."""
    return {
        "event": "poll.vote",
        "session": session,
        "payload": {
            "vote": {
                "fromMe": False,
                "from": sender,
                "timestamp": timestamp,
                "selectedOptions": selected_options or [],
            },
            "poll": {
                "fromMe": True,
                "id": message_id,
                "to": poll_chat_id,
            },
        },
    }


def test_one_action_adds_non_triggering_option() -> None:
    """WhatsApp gets a valid two-option poll for a single real action."""
    options = polls.build_poll_options(
        [{"action": "CANCEL_SLEEP_MODE", "title": "Cancel", "uri": "ignored"}],
        "Keep scheduled",
    )

    assert [(item.title, item.action) for item in options] == [
        ("Cancel", "CANCEL_SLEEP_MODE"),
        ("Keep scheduled", None),
    ]


def test_multiple_actions_preserve_existing_action_ids() -> None:
    """Companion action IDs pass through without translation."""
    options = polls.build_poll_options(
        [
            {"action": "AWAY_CLIMATE_OFF", "title": "Turn off"},
            {"action": "AWAY_CLIMATE_AWAY_PRESET", "title": "Away preset"},
            {"action": "AWAY_CLIMATE_LEAVE_AS_IS", "title": "Leave as is"},
        ],
        "No action",
    )

    assert [item.action for item in options] == [
        "AWAY_CLIMATE_OFF",
        "AWAY_CLIMATE_AWAY_PRESET",
        "AWAY_CLIMATE_LEAVE_AS_IS",
    ]


@pytest.mark.parametrize(
    ("actions", "no_action_title"),
    [
        ([], "No action"),
        ([{"action": "", "title": "Cancel"}], "No action"),
        ([{"action": "CANCEL", "title": ""}], "No action"),
        (
            [
                {"action": "ONE", "title": "Same"},
                {"action": "TWO", "title": "Same"},
            ],
            "No action",
        ),
        ([{"action": "CANCEL", "title": "No action"}], "No action"),
        ([{"action": "REPLY", "title": "Reply"}], "No action"),
        (
            [{"action": "CUSTOM", "title": "Reply", "behavior": "textInput"}],
            "No action",
        ),
    ],
)
def test_invalid_poll_options_are_rejected(actions, no_action_title) -> None:
    """Ambiguous or unrepresentable action sets fail before sending."""
    with pytest.raises(polls.PollValidationError):
        polls.build_poll_options(actions, no_action_title)


def test_parse_vote_accepts_selection_and_deselection() -> None:
    """WAHA selections and explicit vote removal are parsed safely."""
    selected = polls.parse_poll_vote(vote_payload(selected_options=["Cancel"]), "house")
    deselected = polls.parse_poll_vote(vote_payload(timestamp=101), "house")

    assert selected.selected_title == "Cancel"
    assert selected.message_id == "poll-1"
    assert deselected.selected_title is None


@pytest.mark.parametrize(
    "payload",
    [
        vote_payload(session="other"),
        vote_payload(selected_options=["One", "Two"]),
        {
            **vote_payload(selected_options=["Cancel"]),
            "event": "message",
        },
    ],
)
def test_parse_vote_rejects_irrelevant_or_multiple_selection(payload) -> None:
    """Only single-choice votes from the configured session are accepted."""
    assert polls.parse_poll_vote(payload, "house") is None


def test_parse_vote_reports_safe_rejection_reason() -> None:
    """Malformed authenticated callbacks can be diagnosed without identifiers."""
    vote, reason = polls.parse_poll_vote_with_reason(
        vote_payload(selected_options=["One", "Two"]), "house"
    )

    assert vote is None
    assert reason is polls.PollVoteRejectionReason.MULTIPLE_SELECTIONS


def test_registry_debounces_to_newest_vote_and_commits_once() -> None:
    """A quick correction replaces the first choice before publication."""
    registry = polls.PollRegistry()
    options = polls.build_poll_options(
        [
            {"action": "TURN_OFF", "title": "Turn off"},
            {"action": "LEAVE_ON", "title": "Leave on"},
        ],
        "No action",
    )
    registry.register("poll-1", "393331234567@c.us", options, 5)

    first = polls.parse_poll_vote(
        vote_payload(selected_options=["Turn off"], timestamp=100), "house"
    )
    corrected = polls.parse_poll_vote(
        vote_payload(selected_options=["Leave on"], timestamp=101), "house"
    )
    assert registry.apply_vote(first, received_at=1000)
    assert registry.apply_vote(corrected, received_at=1002)

    assert registry.commit("poll-1", 100, now=1007) == (False, None)
    assert registry.commit("poll-1", 101, now=1006.9) == (False, None)
    assert registry.commit("poll-1", 101, now=1007) == (True, "LEAVE_ON")
    assert registry.commit("poll-1", 101, now=1008) == (False, None)


def test_registry_rejects_wrong_recipient_unknown_option_and_old_vote() -> None:
    """Message ID alone cannot authorize an action and old votes cannot win."""
    registry = polls.PollRegistry()
    options = polls.build_poll_options(
        [{"action": "CANCEL", "title": "Cancel"}], "Keep scheduled"
    )
    registry.register("poll-1", "393331234567@c.us", options, 5)

    wrong_sender = polls.parse_poll_vote(
        vote_payload(selected_options=["Cancel"], sender="390000000000@c.us"),
        "house",
    )
    unknown = polls.parse_poll_vote(
        vote_payload(selected_options=["Unexpected"], timestamp=101), "house"
    )
    valid = polls.parse_poll_vote(
        vote_payload(selected_options=["Cancel"], timestamp=102), "house"
    )
    old = polls.parse_poll_vote(
        vote_payload(selected_options=["Keep scheduled"], timestamp=101), "house"
    )

    assert not registry.apply_vote(wrong_sender, received_at=1000)
    assert not registry.apply_vote(unknown, received_at=1000)
    assert registry.apply_vote(valid, received_at=1000)
    assert not registry.apply_vote(old, received_at=1001)


def test_registry_correlates_gows_poll_after_verified_lid_resolution() -> None:
    """GOWS may change both the ID envelope and chat identity to a LID."""
    registry = polls.PollRegistry()
    options = polls.build_poll_options(
        [{"action": "CANCEL", "title": "Cancel"}], "Keep scheduled"
    )
    registry.register(
        "true_393331234567@c.us_A1B2C3D4",
        "393331234567@c.us",
        options,
        5,
    )
    gows_vote = polls.parse_poll_vote(
        vote_payload(
            selected_options=["Cancel"],
            sender="178563278901234@lid",
            poll_chat_id="178563278901234@lid",
            message_id="true_178563278901234@lid_A1B2C3D4",
        ),
        "house",
    )
    resolved_vote = polls.resolve_vote_lids(
        gows_vote,
        {"178563278901234@lid": "393331234567@c.us"},
    )

    assert resolved_vote is not None
    assert registry.apply_vote_with_reason(resolved_vote, received_at=1000) is None
    assert registry.commit("true_178563278901234@lid_A1B2C3D4", 100, now=1005) == (
        True,
        "CANCEL",
    )


def test_registry_does_not_trust_an_unverified_lid() -> None:
    """A syntactically valid LID still requires WAHA's phone-number mapping."""
    vote = polls.parse_poll_vote(
        vote_payload(
            selected_options=["Cancel"],
            sender="178563278901234@lid",
            poll_chat_id="178563278901234@lid",
        ),
        "house",
    )

    assert polls.resolve_vote_lids(vote, {}) is None


def test_registry_rejects_lid_mapped_to_another_contact() -> None:
    """An authoritative mapping still has to equal the configured recipient."""
    registry = polls.PollRegistry()
    options = polls.build_poll_options(
        [{"action": "CANCEL", "title": "Cancel"}], "Keep scheduled"
    )
    registry.register(
        "true_393331234567@c.us_A1B2C3D4",
        "393331234567@c.us",
        options,
        5,
    )
    vote = polls.parse_poll_vote(
        vote_payload(
            selected_options=["Cancel"],
            sender="178563278901234@lid",
            poll_chat_id="178563278901234@lid",
            message_id="true_178563278901234@lid_A1B2C3D4",
        ),
        "house",
    )
    resolved_vote = polls.resolve_vote_lids(
        vote,
        {"178563278901234@lid": "390000000000@c.us"},
    )

    assert resolved_vote is not None
    assert (
        registry.apply_vote_with_reason(resolved_vote, received_at=1000)
        is polls.PollVoteRejectionReason.POLL_CHAT_MISMATCH
    )


@pytest.mark.parametrize(
    ("message_id", "expected"),
    [
        ("true_393331234567@c.us_A1B2C3D4", "A1B2C3D4"),
        ("true_178563278901234@lid_A1B2C3D4", "A1B2C3D4"),
        (
            "false_120363000000@g.us_A1B2C3D4_393331234567@c.us",
            "A1B2C3D4",
        ),
        ("A1B2C3D4", "A1B2C3D4"),
    ],
)
def test_canonical_message_id_uses_engine_stable_token(message_id, expected) -> None:
    """WAHA route envelopes do not change the underlying WhatsApp message ID."""
    assert polls.canonical_message_id(message_id) == expected


@pytest.mark.parametrize(
    ("sender", "poll_chat_id", "expected_reason"),
    [
        (
            "390000000000@c.us",
            "393331234567@c.us",
            polls.PollVoteRejectionReason.SENDER_IDENTITY_MISMATCH,
        ),
        (
            "not-a-number@lid",
            "393331234567@c.us",
            polls.PollVoteRejectionReason.SENDER_IDENTITY_MISMATCH,
        ),
        (
            "178563278901234@lid",
            "390000000000@c.us",
            polls.PollVoteRejectionReason.POLL_CHAT_MISMATCH,
        ),
        (
            "178563278901234@lid",
            "123456789@g.us",
            polls.PollVoteRejectionReason.POLL_CHAT_MISMATCH,
        ),
    ],
)
def test_registry_preserves_direct_poll_destination_security(
    sender, poll_chat_id, expected_reason
) -> None:
    """LID support never weakens exact destination or non-LID identity checks."""
    registry = polls.PollRegistry()
    options = polls.build_poll_options(
        [{"action": "CANCEL", "title": "Cancel"}], "Keep scheduled"
    )
    registry.register("poll-1", "393331234567@c.us", options, 5)
    vote = polls.parse_poll_vote(
        vote_payload(
            selected_options=["Cancel"],
            sender=sender,
            poll_chat_id=poll_chat_id,
        ),
        "house",
    )

    assert registry.apply_vote_with_reason(vote, received_at=1000) is expected_reason


def test_no_action_selection_is_consumed_without_action() -> None:
    """The synthetic option closes correlation but never fires an action."""
    registry = polls.PollRegistry()
    options = polls.build_poll_options(
        [{"action": "CANCEL", "title": "Cancel"}], "Keep scheduled"
    )
    registry.register("poll-1", "393331234567@c.us", options, 5)
    vote = polls.parse_poll_vote(
        vote_payload(selected_options=["Keep scheduled"]), "house"
    )

    assert registry.apply_vote(vote, received_at=1000)
    assert registry.commit("poll-1", 100, now=1005) == (True, None)


def test_pending_poll_storage_round_trip() -> None:
    """A restart preserves correlation and the latest correction window."""
    original = polls.PollRegistry()
    options = polls.build_poll_options(
        [{"action": "CANCEL", "title": "Cancel"}], "Keep scheduled"
    )
    original.register(
        "poll-1",
        "393331234567@c.us",
        options,
        5,
        person_entity_id="person.seba",
    )
    vote = polls.parse_poll_vote(
        vote_payload(selected_options=["Cancel"], timestamp=123), "house"
    )
    original.apply_vote(vote, received_at=1000)

    restored = polls.PollRegistry()
    restored.load(original.as_dict())

    pending = restored.pending("poll-1")
    assert pending is not None
    assert pending.person_entity_id == "person.seba"
    assert "user_id" not in pending.as_dict()
    assert restored.commit("poll-1", 123, now=1005) == (True, "CANCEL")


def test_legacy_pending_poll_id_is_canonicalized_during_restore() -> None:
    """Updating keeps pre-1.2.2 polls correlatable across ID envelopes."""
    registry = polls.PollRegistry()
    registry.load(
        {
            "polls": [
                {
                    "message_id": "true_393331234567@c.us_A1B2C3D4",
                    "chat_id": "393331234567@c.us",
                    "options": {"Cancel": "CANCEL", "Keep scheduled": None},
                    "settle_seconds": 5,
                    "latest_vote_timestamp": None,
                    "selected_title": None,
                    "commit_at": None,
                }
            ]
        }
    )

    pending = registry.pending("true_178563278901234@lid_A1B2C3D4")
    assert pending is not None
    assert pending.message_id == "A1B2C3D4"
    assert pending.person_entity_id is None
