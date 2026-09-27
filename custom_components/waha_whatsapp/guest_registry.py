"""Private, fail-closed identity and membership state for a managed WAHA group.

This module performs no WAHA writes. Callers must verify the bot account, group
security settings, and authoritative roster before enabling guest routing.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

STORAGE_VERSION = 1
MAX_CLOSED_MEMBERSHIPS = 512
CLOSED_RETENTION_SECONDS = 180 * 24 * 60 * 60
VALID_ROLES = frozenset({"participant", "admin", "superadmin"})
_JID = re.compile(r"^[0-9]{1,20}@(c\.us|s\.whatsapp\.net|lid)$", re.I)
_GROUP_JID = re.compile(r"^[0-9]{1,20}(?:-[0-9]{1,20})?@g\.us$", re.I)


def _opaque(value: Any, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(rf"{re.escape(prefix)}_[0-9a-f]{{32}}", value) is not None
    )


def _jid(value: str, *, group: bool = False) -> str:
    """Canonicalize a JID without inventing a PN/LID identity association."""
    pattern = _GROUP_JID if group else _JID
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("Unverified or malformed WhatsApp identifier")
    value = value.lower()
    if value.endswith("@s.whatsapp.net"):
        value = value.split("@", 1)[0] + "@c.us"
    return value


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """Persisted transition; safe to turn into a public event."""

    type: str
    participant_id: str
    membership_id: str
    observed_at: float
    occurred_at: float | None
    source: str


class GuestRegistry:
    """One entry's private group, aliases, membership intervals, and routes."""

    def __init__(self, hass: Any, entry_id: str, *, store: Any | None = None) -> None:
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("Entry ID is required")
        if store is None:
            from homeassistant.helpers.storage import Store

            store = Store[dict[str, Any]](
                hass,
                STORAGE_VERSION,
                f"waha_whatsapp.guest.{entry_id}",
                private=True,
                atomic_writes=True,
            )
        self._store = store
        self._lock = asyncio.Lock()
        self._data = self._empty()
        self.storage_healthy = False
        self.confirmed = False  # Restored state is never a live roster proof.
        self._create_started_here = False

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": STORAGE_VERSION,
            "account": None,
            "group": None,
            "participants": {},
            "memberships": {},
            "revision": 0,
            "observed_at": None,
            "provisioning_status": "unconfigured",
        }

    @staticmethod
    def _timestamp(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Invalid timestamp")
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid timestamp")
        return value

    async def async_start(self) -> None:
        """Restore validated state; absent or unreadable storage disables routing."""
        self.storage_healthy = False
        self.confirmed = False
        self._create_started_here = False
        try:
            loaded = await self._store.async_load()
            if loaded is None:
                self._data = self._empty()
                return
            self._validate(loaded)
        except Exception:
            self._data = self._empty()
            return
        self._data = deepcopy(loaded)
        self.storage_healthy = True

    async def async_initialize_empty(self) -> None:
        """Persist pristine state only at the first explicit enable confirmation.

        Never replace an existing registry, including corrupt state: it may
        contain the sole record of a previously created WhatsApp group.
        """
        async with self._lock:
            loaded = await self._store.async_load()
            if loaded is not None:
                self._validate(loaded)
                self._data = deepcopy(loaded)
                self.storage_healthy = True
                return
            empty = self._empty()
            await self._store.async_save(empty)
            self._data = empty
            self.storage_healthy = True

    @staticmethod
    def _validate(data: Any) -> None:
        if not isinstance(data, dict) or data.get("version") != STORAGE_VERSION:
            raise ValueError("Invalid guest registry version")
        if not isinstance(data.get("participants"), dict) or not isinstance(
            data.get("memberships"), dict
        ):
            raise ValueError("Invalid registry collections")
        if not isinstance(data.get("revision"), int) or data["revision"] < 0:
            raise ValueError("Invalid registry revision")
        if data.get("observed_at") is not None:
            GuestRegistry._timestamp(data["observed_at"])
        if data.get("provisioning_status") not in (
            "unconfigured",
            "creating",
            "create_unknown",
            "group_saved",
            "ready",
            "suspended",
        ):
            raise ValueError("Invalid provisioning status")
        if data.get("account") is not None:
            _jid(data["account"])
        elif data["provisioning_status"] != "unconfigured":
            raise ValueError("Unbound account has provisioning state")
        group = data.get("group")
        if group is not None:
            if not isinstance(group, dict) or not _opaque(group.get("id"), "group"):
                raise ValueError("Invalid group")
            _jid(group.get("jid"), group=True)
            if not _opaque(group.get("conversation_id"), "group_conversation"):
                raise ValueError("Invalid group conversation")
            if data["provisioning_status"] in (
                "unconfigured",
                "creating",
                "create_unknown",
            ):
                raise ValueError("Inconsistent group provisioning state")
        elif (
            data["provisioning_status"] in ("group_saved", "ready", "suspended")
            or data["participants"]
            or data["memberships"]
        ):
            raise ValueError("Group state without saved group")
        aliases: dict[str, str] = {}
        for participant_id, participant in data["participants"].items():
            if (
                not isinstance(participant_id, str)
                or not _opaque(participant_id, "participant")
                or not isinstance(participant, dict)
            ):
                raise ValueError("Invalid participant")
            if (
                not isinstance(participant.get("aliases"), list)
                or not participant["aliases"]
            ):
                raise ValueError("Invalid aliases")
            for alias in participant["aliases"]:
                canonical = _jid(alias)
                if canonical != alias or alias in aliases:
                    raise ValueError("Conflicting or noncanonical alias")
                aliases[alias] = participant_id
        active: set[str] = set()
        routes: set[str] = set()
        for membership_id, member in data["memberships"].items():
            if (
                not isinstance(membership_id, str)
                or not _opaque(membership_id, "membership")
                or not isinstance(member, dict)
            ):
                raise ValueError("Invalid membership")
            if member.get("participant_id") not in data["participants"] or member.get(
                "kind"
            ) not in ("host", "guest"):
                raise ValueError("Invalid membership identity")
            if member.get("status") not in ("active", "left"):
                raise ValueError("Invalid membership interval")
            GuestRegistry._timestamp(member.get("started_at"))
            if member["status"] == "active":
                if member["participant_id"] in active:
                    raise ValueError("Duplicate active membership")
                active.add(member["participant_id"])
            elif (
                GuestRegistry._timestamp(member.get("ended_at")) < member["started_at"]
            ):
                raise ValueError("Invalid closed interval")
            if (
                not isinstance(member.get("route"), str)
                or not _opaque(member["route"], "guest_direct")
                or member["route"] in routes
            ):
                raise ValueError("Invalid route")
            routes.add(member["route"])
            if member.get("role") not in VALID_ROLES:
                raise ValueError("Invalid role")

    async def _save(self, updated: dict[str, Any]) -> None:
        if not self.storage_healthy:
            raise RuntimeError("Guest registry storage unavailable")
        try:
            await self._store.async_save(updated)
        except Exception:
            self.storage_healthy = False
            self.confirmed = False
            raise
        self._data = updated

    def account_matches(self, verified_bot_jid: str) -> bool:
        """Require a positively verified account; a session name is insufficient."""
        try:
            account = _jid(verified_bot_jid)
        except ValueError:
            return False
        return self.storage_healthy and self._data["account"] == account

    def group_matches(self, raw_group_jid: str, verified_bot_jid: str) -> bool:
        """Recognize only the saved group under its bound bot account."""
        try:
            group_jid = _jid(raw_group_jid, group=True)
        except ValueError:
            return False
        group = self._data["group"]
        return (
            self.account_matches(verified_bot_jid)
            and group is not None
            and group["jid"] == group_jid
        )

    @property
    def saved_group_jid(self) -> str | None:
        """Private exact group destination for lifecycle readback, not events."""
        group = self._data["group"]
        return group["jid"] if self.storage_healthy and group is not None else None

    @property
    def provisioning_status(self) -> str:
        return self._data["provisioning_status"]

    @property
    def bound_account_jid(self) -> str | None:
        return self._data["account"] if self.storage_healthy else None

    async def async_bind_account(self, verified_bot_jid: str) -> None:
        """Bind once; changing account requires a separate reviewed recovery."""
        account = _jid(verified_bot_jid)
        async with self._lock:
            current = self._data["account"]
            if current is not None and current != account:
                self.confirmed = False
                raise ValueError("Managed group belongs to another bot account")
            updated = deepcopy(self._data)
            updated["account"] = account
            await self._save(updated)

    async def async_set_group(self, raw_group_jid: str) -> str:
        """Persist an exact, confirmed group after external provisioning."""
        jid = _jid(raw_group_jid, group=True)
        async with self._lock:
            if self._data["account"] is None:
                raise ValueError("Bot account is not bound")
            if self._data["provisioning_status"] == "create_unknown" or (
                self._data["provisioning_status"] == "creating"
                and not self._create_started_here
            ):
                raise ValueError("Unknown create outcome requires confirmed recovery")
            current = self._data["group"]
            if current is not None:
                if current["jid"] != jid:
                    raise ValueError("Replacing a group requires reviewed recovery")
                return current["id"]
            updated = deepcopy(self._data)
            updated["group"] = {
                "id": _id("group"),
                "jid": jid,
                "conversation_id": _id("group_conversation"),
            }
            updated["provisioning_status"] = "group_saved"
            await self._save(updated)
            self._create_started_here = False
            return updated["group"]["id"]

    async def async_mark_create_started(self) -> None:
        """Record the external-create attempt before sending the WAHA request."""
        async with self._lock:
            if self._data["group"] is not None or self._data[
                "provisioning_status"
            ] not in ("unconfigured",):
                raise ValueError("Group creation must not be retried automatically")
            updated = deepcopy(self._data)
            updated["provisioning_status"] = "creating"
            await self._save(updated)
            self._create_started_here = True

    async def async_mark_create_unknown(self) -> None:
        """Require explicit recovery when a create result could not be proven."""
        async with self._lock:
            if self._data["provisioning_status"] != "creating":
                raise ValueError("No pending group creation")
            updated = deepcopy(self._data)
            updated["provisioning_status"] = "create_unknown"
            await self._save(updated)
            self._create_started_here = False
            self.confirmed = False

    async def async_adopt_unknown_group(
        self, raw_group_jid: str, *, verified_bot_jid: str
    ) -> str:
        """Record an explicitly reviewed exact group without provisioning writes.

        Adoption leaves the group suspended. Only a separate, fresh read-only
        reconciliation can make guest routes available.
        """
        jid = _jid(raw_group_jid, group=True)
        account = _jid(verified_bot_jid)
        async with self._lock:
            if (
                not self.storage_healthy
                or self._data["provisioning_status"]
                not in ("creating", "create_unknown")
                or self._data["group"] is not None
                or self._data["account"] != account
            ):
                raise ValueError("Unknown create outcome or bot account changed")
            updated = deepcopy(self._data)
            updated["group"] = {
                "id": _id("group"),
                "jid": jid,
                "conversation_id": _id("group_conversation"),
            }
            updated["provisioning_status"] = "suspended"
            await self._save(updated)
            self._create_started_here = False
            self.confirmed = False
            return updated["group"]["id"]

    def suspend(self) -> None:
        """Stop group-derived routing when external readiness is lost."""
        self.confirmed = False

    async def async_set_provisioning_status(self, status: str) -> None:
        """Persist externally verified readiness without creating a group."""
        if status not in ("group_saved", "ready", "suspended"):
            raise ValueError("Invalid provisioning transition")
        async with self._lock:
            if self._data["group"] is None:
                raise ValueError("No managed group")
            updated = deepcopy(self._data)
            updated["provisioning_status"] = status
            await self._save(updated)
            if status != "ready":
                self.confirmed = False

    def _alias_owner(self, alias: str, data: dict[str, Any]) -> str | None:
        return next(
            (pid for pid, p in data["participants"].items() if alias in p["aliases"]),
            None,
        )

    def _member(
        self, data: dict[str, Any], participant_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        return next(
            (
                (mid, m)
                for mid, m in data["memberships"].items()
                if m["participant_id"] == participant_id and m["status"] == "active"
            ),
            None,
        )

    def _resolve_identity(self, data: dict[str, Any], aliases: Iterable[str]) -> str:
        normalized = {_jid(alias) for alias in aliases}
        if not normalized:
            raise ValueError("Verified alias required")
        owners = {
            owner for alias in normalized if (owner := self._alias_owner(alias, data))
        }
        if len(owners) > 1:
            raise ValueError("Conflicting verified aliases")
        participant_id = next(iter(owners)) if owners else _id("participant")
        current = set(data["participants"].get(participant_id, {}).get("aliases", []))
        data["participants"][participant_id] = {"aliases": sorted(current | normalized)}
        return participant_id

    async def async_reconcile(
        self,
        roster: Iterable[Mapping[str, Any]],
        *,
        observed_at: float | None = None,
        verified_bot_jid: str,
        security_verified: bool = False,
        bot_admin_verified: bool = False,
        event_hints: Iterable[Mapping[str, Any]] = (),
    ) -> list[MembershipChange]:
        """Persist an authoritative roster; return differences for event publication.

        Each roster item has ``aliases`` (verified JIDs), ``kind`` (host/guest),
        and ``role``. Snapshot-discovered joins start at observation time, never
        at an invented historical time. Duplicate/conflicting identity aborts.
        ``event_hints`` are already authenticated participant-change events,
        not join requests. Only a hint matching a roster transition is used.
        """
        self.confirmed = False
        if (
            not self.account_matches(verified_bot_jid)
            or self._data["group"] is None
            or not security_verified
            or not bot_admin_verified
        ):
            raise ValueError("Account, group security, or bot role not verified")
        when = self._timestamp(observed_at if observed_at is not None else time.time())
        if when > time.time() + 300:
            raise ValueError("Invalid observation time")
        async with self._lock:
            updated = deepcopy(self._data)
            previous = updated["observed_at"]
            if previous is not None and when < previous:
                raise ValueError("Out-of-order roster")
            hints: dict[str, tuple[str, float]] = {}
            for hint in event_hints:
                if not isinstance(hint, Mapping) or hint.get("type") not in (
                    "join",
                    "leave",
                    "promote",
                    "demote",
                ):
                    raise ValueError("Invalid participant event hint")
                alias = _jid(hint.get("alias"))
                occurred = self._timestamp(hint.get("occurred_at"))
                # A delayed webhook can predate the last authoritative read.
                # Its hint is stale, but the new roster is still authoritative.
                if previous is not None and occurred < previous:
                    continue
                if alias in hints or occurred > when:
                    raise ValueError("Out-of-order or duplicate participant event hint")
                hints[alias] = (hint["type"], occurred)

            def provenance(
                aliases: Iterable[str], expected: str
            ) -> tuple[str, float | None]:
                if previous is None:
                    return "reconciliation", None
                matches = {
                    hints[a] for a in aliases if a in hints and hints[a][0] == expected
                }
                if len(matches) == 1:
                    return "webhook", next(iter(matches))[1]
                return "reconciliation", None

            seen: set[str] = set()
            changes: list[MembershipChange] = []
            for item in roster:
                if (
                    not isinstance(item, Mapping)
                    or item.get("kind") not in ("host", "guest")
                    or item.get("role") not in VALID_ROLES
                ):
                    raise ValueError("Invalid roster member")
                # Roster aliases are identity assertions only when the caller
                # positively verified their mapping. A bare roster JID is one
                # alias; do not merge it with a PN/LID merely by proximity.
                if item.get("aliases_verified") is not True:
                    raw_aliases = item.get("aliases", ())
                    if (
                        not isinstance(raw_aliases, (list, tuple))
                        or len(raw_aliases) != 1
                    ):
                        raise ValueError("Alias mapping is not verified")
                participant_id = self._resolve_identity(
                    updated, item.get("aliases", ())
                )
                if any(
                    alias == self._data["account"]
                    for alias in updated["participants"][participant_id]["aliases"]
                ):
                    continue  # Bot is not a human guest or host.
                if participant_id in seen:
                    raise ValueError("Duplicate roster identity")
                seen.add(participant_id)
                current = self._member(updated, participant_id)
                if current is None:
                    membership_id = _id("membership")
                    source, occurred = provenance(
                        updated["participants"][participant_id]["aliases"], "join"
                    )
                    updated["memberships"][membership_id] = {
                        "participant_id": participant_id,
                        "kind": item["kind"],
                        "role": item["role"],
                        "status": "active",
                        "started_at": occurred if occurred is not None else when,
                        "ended_at": None,
                        "route": _id("guest_direct"),
                    }
                    # First snapshot is a baseline, not a synthetic historical join.
                    if previous is not None:
                        changes.append(
                            MembershipChange(
                                "joined",
                                participant_id,
                                membership_id,
                                when,
                                occurred,
                                source,
                            )
                        )
                else:
                    membership_id, member = current
                    if member["kind"] != item["kind"] or member["role"] != item["role"]:
                        expected = (
                            "promote"
                            if item["role"] in ("admin", "superadmin")
                            else "demote"
                        )
                        source, occurred = provenance(
                            updated["participants"][participant_id]["aliases"], expected
                        )
                        member["kind"], member["role"] = item["kind"], item["role"]
                        changes.append(
                            MembershipChange(
                                "role_changed",
                                participant_id,
                                membership_id,
                                when,
                                occurred,
                                source,
                            )
                        )
            for membership_id, member in updated["memberships"].items():
                if (
                    member["status"] == "active"
                    and member["participant_id"] not in seen
                ):
                    source, occurred = provenance(
                        updated["participants"][member["participant_id"]]["aliases"],
                        "leave",
                    )
                    member["status"] = "left"
                    member["ended_at"] = occurred if occurred is not None else when
                    changes.append(
                        MembershipChange(
                            "left",
                            member["participant_id"],
                            membership_id,
                            when,
                            occurred,
                            source,
                        )
                    )
            updated["observed_at"] = when
            updated["revision"] += 1
            self._prune(updated, when)
            await self._save(updated)
            self.confirmed = True
            return changes

    @staticmethod
    def _prune(data: dict[str, Any], now: float) -> None:
        """Retain active records; bound closed tombstones by age and count."""
        closed = sorted(
            (
                (mid, m)
                for mid, m in data["memberships"].items()
                if m["status"] == "left"
            ),
            key=lambda item: item[1]["ended_at"],
            reverse=True,
        )
        for mid, _member in closed[MAX_CLOSED_MEMBERSHIPS:]:
            del data["memberships"][mid]
        for mid, member in closed[:MAX_CLOSED_MEMBERSHIPS]:
            if member["ended_at"] < now - CLOSED_RETENTION_SECONDS:
                data["memberships"].pop(mid, None)
        retained = {member["participant_id"] for member in data["memberships"].values()}
        for participant_id in list(data["participants"]):
            if participant_id not in retained:
                del data["participants"][participant_id]

    def resolve_member(
        self, alias: str, *, occurred_at: float | None = None
    ) -> dict[str, Any] | None:
        """Resolve only an unambiguous confirmed current member and interval."""
        if (
            not self.storage_healthy
            or not self.confirmed
            or self._data["provisioning_status"] != "ready"
            or occurred_at is None
        ):
            return None
        try:
            owner = self._alias_owner(_jid(alias), self._data)
        except ValueError:
            return None
        if owner is None:
            return None
        current = self._member(self._data, owner)
        if current is None:
            return None
        mid, member = current
        if occurred_at < member["started_at"] or occurred_at > time.time() + 300:
            return None
        return self._public_member(mid, member)

    def resolve_route(self, conversation_id: str) -> str | None:
        """Return private JID only for a currently confirmed membership route."""
        if (
            not self.storage_healthy
            or not self.confirmed
            or self._data["provisioning_status"] != "ready"
        ):
            return None
        for member in self._data["memberships"].values():
            if member["status"] != "active" or member["route"] != conversation_id:
                continue
            aliases = self._data["participants"][member["participant_id"]]["aliases"]
            # A PN destination must be verified; LID-only identities cannot send.
            phones = [alias for alias in aliases if alias.endswith("@c.us")]
            return phones[0] if len(phones) == 1 else None
        return None

    def resolve_group_conversation(self, conversation_id: str) -> str | None:
        """Return the exact group destination only when the feature is ready."""
        group = self._data["group"]
        if (
            not self.storage_healthy
            or not self.confirmed
            or self._data["provisioning_status"] != "ready"
            or group is None
            or group["conversation_id"] != conversation_id
        ):
            return None
        return group["jid"]

    async def async_with_route(
        self,
        conversation_id: str,
        verify_current: Callable[[str], Awaitable[bool]],
        send: Callable[[str], Awaitable[Any]],
    ) -> Any:
        """Verify and send under the same lock used by local roster changes.

        ``verify_current`` must perform a fresh WAHA membership/security check;
        the stored snapshot alone is not enough. The unavoidable external
        remove/send race remains the caller's documented limitation.
        """
        async with self._lock:
            destination = self.resolve_route(conversation_id)
            if destination is None or not await verify_current(destination):
                raise ValueError("Guest route is no longer verified")
            return await send(destination)

    def _public_member(self, mid: str, member: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            "participant_id": member["participant_id"],
            "membership_id": mid,
            "kind": member["kind"],
            "status": member["status"],
            "whatsapp_role": member["role"],
        }
        if member["status"] == "active":
            result["direct_conversation_id"] = member["route"]
        return result

    def snapshot(self, *, membership_id: str | None = None) -> dict[str, Any]:
        """Return a phone-free query result; restored state is unavailable."""
        group = self._data["group"]
        members = self._data["memberships"]
        chosen = (
            {membership_id: members[membership_id]}
            if membership_id in members
            else ({} if membership_id else members)
        )
        result = {
            "group_id": group["id"] if group else None,
            "conversation_id": group["conversation_id"] if group else None,
            "ready": self.storage_healthy
            and self.confirmed
            and group is not None
            and self._data["provisioning_status"] == "ready",
            "confirmed": self.storage_healthy and self.confirmed,
            "provisioning_status": self._data["provisioning_status"],
            "revision": self._data["revision"],
            "observed_at": self._data["observed_at"],
            "memberships": [
                self._public_member(mid, member) for mid, member in chosen.items()
            ],
        }
        if membership_id is not None:
            result["membership_found"] = membership_id in members
        return result
