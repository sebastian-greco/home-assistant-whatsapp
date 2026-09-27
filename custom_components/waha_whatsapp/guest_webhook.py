"""Strict, side-effect-free parsing of managed-group WAHA callback hints.

Webhooks announce possible changes; only a fresh authoritative roster may
establish or end a membership. A join request is never a joined participant.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_GROUP_JID = re.compile(r"^[0-9]{1,20}(?:-[0-9]{1,20})?@g\.us$")
_PARTICIPANT_JID = re.compile(r"^[0-9]{1,20}@(c\.us|s\.whatsapp\.net|lid)$")
_CHANGES = {"join", "leave", "promote", "demote"}
_ROLES = {"left", "participant", "admin", "superadmin"}
MAX_CHANGED_PARTICIPANTS = 256
MAX_EVENT_ID_LENGTH = 512


@dataclass(frozen=True, slots=True)
class ParticipantHint:
    """A webhook hint to compare against a later confirmed roster difference."""

    event_id: str
    group_jid: str
    change_type: str
    occurred_at: float | None
    participants: tuple[tuple[str, str | None], ...]


def participant_hint(
    data: Mapping[str, Any], *, session: str, group_jid: str
) -> ParticipantHint | None:
    """Parse a changed-participants callback for the one exact saved group."""
    if data.get("event") != "group.v2.participants" or data.get("session") != session:
        return None
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return None
    group = payload.get("group")
    if (
        not isinstance(group, Mapping)
        or group.get("id") != group_jid
        or not _GROUP_JID.fullmatch(group_jid)
    ):
        return None
    event_id = data.get("id")
    if not isinstance(event_id, str) or not 0 < len(event_id) <= MAX_EVENT_ID_LENGTH:
        return None
    change_type = payload.get("type")
    if change_type not in _CHANGES:
        return None
    participants = payload.get("participants")
    if (
        not isinstance(participants, list)
        or not 0 < len(participants) <= MAX_CHANGED_PARTICIPANTS
    ):
        return None
    changed = []
    for participant in participants:
        if not isinstance(participant, Mapping):
            return None
        jid = participant.get("id")
        pn = participant.get("pn")
        role = participant.get("role")
        if (
            not isinstance(jid, str)
            or not _PARTICIPANT_JID.fullmatch(jid)
            or (
                pn is not None
                and (
                    not isinstance(pn, str)
                    or not pn.endswith("@c.us")
                    or not _PARTICIPANT_JID.fullmatch(pn)
                )
            )
            or role not in _ROLES
        ):
            return None
        changed.append((jid, pn))
    timestamp = _timestamp(payload.get("timestamp"))
    # The callback is still useful as a refresh trigger when its occurrence
    # time is missing, stale, or implausibly future-dated. It cannot then
    # authoritatively time a membership transition.
    now = time.time()
    if timestamp is None or not now - 3600 <= timestamp <= now + 300:
        timestamp = None
    return ParticipantHint(event_id, group_jid, change_type, timestamp, tuple(changed))


def is_managed_group_signal(
    data: Mapping[str, Any], *, session: str, group_jid: str
) -> bool:
    """Whether a callback warrants roster/security re-verification.

    Join requests are intentionally excluded: they confer no membership.
    """
    if data.get("session") != session or data.get("event") not in (
        "group.v2.join",
        "group.v2.leave",
        "group.v2.participants",
        "group.v2.update",
    ):
        return False
    payload = data.get("payload")
    group = payload.get("group") if isinstance(payload, Mapping) else None
    return (
        isinstance(group, Mapping)
        and group.get("id") == group_jid
        and bool(_GROUP_JID.fullmatch(group_jid))
    )


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    seconds = value / 1000 if value >= 1e11 else value
    return seconds if math.isfinite(seconds) else None
