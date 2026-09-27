"""Opt-in lifecycle for one integration-managed WhatsApp guest group.

External writes happen only after the confirmed options flow. A saved group's
normal resume is read-only unless provisioning was incomplete. All routing is
gated by fresh account, role, security, and roster verification.
"""

from __future__ import annotations

import asyncio
import hmac
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .const import (
    CONF_GUEST_GROUP_ENABLED,
    CONF_GUEST_GROUP_EXCLUDED_ADMIN_USER_IDS,
    CONF_GUEST_GROUP_NAME,
    CONF_GUEST_GROUP_REVIEWED_ADMIN_FINGERPRINT,
    CONF_WEBHOOK_SECRET,
    EVENT_WAHA_WHATSAPP,
    SESSION_STATUS_WORKING,
)
from .guest_admins import (
    GuestAdminPreview,
    async_guest_admin_preview,
    reviewed_admin_fingerprint,
)
from .guest_registry import GuestRegistry, MembershipChange, _jid
from .guest_webhook import is_managed_group_signal, participant_hint

MIN_GOWS_VERSION = (2026, 8, 2)
MIN_REFRESH_SECONDS = 15.0
SECURE_SETTINGS = {
    "info_admin_only": True,
    "messages_admin_only": False,
    "members_can_add": False,
    "membership_approval_required": True,
}


class GuestGroupError(Exception):
    """Safe lifecycle failure; ``reason`` contains no account or phone data."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GuestGroupManager:
    """Provision and reconcile one exact saved guest group per config entry."""

    def __init__(
        self,
        hass: Any,
        entry: Any,
        client: Any,
        registry: GuestRegistry,
        *,
        preview_provider: Callable[
            [Any, Any], Awaitable[GuestAdminPreview]
        ] = async_guest_admin_preview,
        publish: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self.registry = registry
        self._preview_provider = preview_provider
        self._publish = publish or self._default_publish
        self._lock = asyncio.Lock()
        self._last_refresh = 0.0
        self._events_available = True
        self.failure_reason: str | None = None

    def _default_publish(self, event: dict[str, Any]) -> None:
        self._hass.bus.async_fire(EVENT_WAHA_WHATSAPP, event)

    def _enabled(self) -> bool:
        return (
            self._events_available
            and self._entry.options.get(CONF_GUEST_GROUP_ENABLED) is True
        )

    def disable_events(self, reason: str) -> None:
        """Fail closed if the required guest webhook cannot be registered."""
        self._events_available = False
        self.suspend(reason)

    async def _prerequisites(self) -> str:
        """Check capabilities and positively identify the active bot account."""
        server = await self._client.async_get_server()
        version = re.fullmatch(
            r"(\d{4})\.(\d{1,2})\.(\d+)(?:\+[0-9A-Za-z.-]+)?", server.version
        )
        if (
            not isinstance(server.engine, str)
            or server.engine.upper() != "GOWS"
            or version is None
            or tuple(map(int, version.groups())) < MIN_GOWS_VERSION
        ):
            raise GuestGroupError("unsupported_waha_capabilities")
        session = await self._client.async_get_session()
        if session.status != SESSION_STATUS_WORKING or not session.account_id:
            raise GuestGroupError("unverified_bot_account")
        return session.account_id

    async def _reviewed_hosts(self) -> tuple[str, ...]:
        """Recheck the user-reviewed, entry-local admin mapping before writes."""
        preview = await self._preview_provider(self._hass, self._entry)
        excluded = self._entry.options.get(CONF_GUEST_GROUP_EXCLUDED_ADMIN_USER_IDS, [])
        if not isinstance(excluded, list) or not all(
            isinstance(value, str) for value in excluded
        ):
            raise GuestGroupError("admin_mapping_changed")
        excluded_set = set(excluded)
        gap_ids = {gap.user_id for gap in preview.gaps}
        if (
            len(excluded_set) != len(excluded)
            or excluded_set != gap_ids
            or not preview.contacts
        ):
            raise GuestGroupError("admin_mapping_changed")
        secret = self._entry.data.get(CONF_WEBHOOK_SECRET)
        expected = self._entry.options.get(CONF_GUEST_GROUP_REVIEWED_ADMIN_FINGERPRINT)
        if not isinstance(expected, str) or not isinstance(secret, str):
            raise GuestGroupError("admin_mapping_changed")
        current = reviewed_admin_fingerprint(preview, excluded_set, secret)
        if not hmac.compare_digest(current, expected):
            raise GuestGroupError("admin_mapping_changed")
        return tuple(contact.chat_id for contact in preview.contacts)

    async def async_review_unknown_group(self, raw_group_jid: str) -> dict[str, str]:
        """Read an exact candidate for explicit recovery; never search or write.

        The returned account and group IDs are shown to the administrator.
        This does not alter the unknown-outcome state or enable guest routing.
        """
        async with self._lock:
            return await self._review_unknown_group_locked(raw_group_jid)

    async def _review_unknown_group_locked(self, raw_group_jid: str) -> dict[str, str]:
        if not self._enabled() or self.registry.provisioning_status not in (
            "creating",
            "create_unknown",
        ):
            raise GuestGroupError("group_recovery_unavailable")
        try:
            group_jid = _jid(raw_group_jid, group=True)
        except ValueError:
            raise GuestGroupError("invalid_recovery_group_id") from None
        try:
            account = await self._prerequisites()
        except GuestGroupError:
            raise
        except Exception:
            raise GuestGroupError("recovery_group_unverified") from None
        if not self.registry.account_matches(account):
            raise GuestGroupError("bot_account_changed")
        try:
            hosts = await self._reviewed_hosts()
        except GuestGroupError:
            raise
        except Exception:
            raise GuestGroupError("admin_mapping_changed") from None
        try:
            await self._client.async_get_group(group_jid)
            security = await self._client.async_get_group_security(group_jid)
            roster = await self._client.async_get_group_participants(group_jid)
        except Exception:
            raise GuestGroupError("recovery_group_unverified") from None
        if security != SECURE_SETTINGS:
            raise GuestGroupError("unsafe_group_settings")
        if not self._bot_is_admin(roster, account):
            raise GuestGroupError("bot_admin_unconfirmed")
        if not all(self._host_is_admin(roster, host) for host in hosts):
            raise GuestGroupError("admin_role_drift")
        if self._unexpected_admin(roster, account, hosts):
            raise GuestGroupError("unexpected_group_admin")
        return {"group_id": group_jid, "bot_account": _jid(account)}

    async def async_adopt_unknown_group(
        self, raw_group_jid: str, *, reviewed_bot_account: str
    ) -> dict[str, Any]:
        """Recheck the reviewed candidate and persist it as suspended only."""
        async with self._lock:
            reviewed = await self._review_unknown_group_locked(raw_group_jid)
            if reviewed["bot_account"] != reviewed_bot_account:
                raise GuestGroupError("bot_account_changed")
            try:
                await self.registry.async_adopt_unknown_group(
                    reviewed["group_id"], verified_bot_jid=reviewed["bot_account"]
                )
            except RuntimeError, ValueError:
                raise GuestGroupError("group_recovery_unavailable") from None
            self.failure_reason = "group_not_ready"
            return self.snapshot()

    async def async_setup(self) -> dict[str, Any]:
        """Resume safely, or create once after the reviewed opt-in confirmation."""
        async with self._lock:
            needs_verify = await self._async_setup_locked()
        if needs_verify:
            # Read-only verification occurs after provisioning releases the lock.
            return await self.async_reconcile(force=True)
        return self.snapshot()

    async def _async_setup_locked(self) -> bool:
        """Serialize provisioning with webhook-triggered reconciliation."""
        await self.registry.async_start()
        if not self._enabled():
            self.registry.suspend()
            if self._events_available:
                self.failure_reason = "disabled"
            return False
        try:
            if not self.registry.storage_healthy:
                raise GuestGroupError("guest_registry_unavailable")
            account = await self._prerequisites()
            hosts = await self._reviewed_hosts()
            if (
                self.registry.saved_group_jid is not None
                and not self.registry.account_matches(account)
            ):
                raise GuestGroupError("bot_account_changed")
            await self.registry.async_bind_account(account)
            group_jid = self.registry.saved_group_jid
            if group_jid is None:
                status = self.registry.snapshot()["provisioning_status"]
                if status in ("creating", "create_unknown"):
                    raise GuestGroupError("group_create_outcome_unknown")
                name = self._entry.options.get(CONF_GUEST_GROUP_NAME)
                if not isinstance(name, str) or not name.strip():
                    raise GuestGroupError("invalid_group_name")
                await self.registry.async_mark_create_started()
                try:
                    group_jid = await self._client.async_create_group(
                        name.strip(), list(hosts)
                    )
                except Exception:
                    await self.registry.async_mark_create_unknown()
                    raise GuestGroupError("group_create_outcome_unknown") from None
                await self.registry.async_set_group(group_jid)
            status = self.registry.snapshot()["provisioning_status"]
            if status == "group_saved":
                await self._finish_provisioning(group_jid, hosts, account)
            else:
                # A previously ready/suspended group is never silently modified.
                return True
            self.failure_reason = None
        except Exception as err:
            self.registry.suspend()
            self.failure_reason = (
                err.reason
                if isinstance(err, GuestGroupError)
                else "guest_group_unavailable"
            )
        return False

    async def _finish_provisioning(
        self, group_jid: str, hosts: tuple[str, ...], account: str
    ) -> None:
        await self._client.async_get_group(group_jid)
        roster = await self._client.async_get_group_participants(group_jid)
        if self._unexpected_admin(roster, account, hosts):
            raise GuestGroupError("unexpected_group_admin")
        present = {
            alias
            for item in roster
            if item.role != "left"
            for alias in self._aliases(item)
        }
        missing = [host for host in hosts if host not in present]
        if missing:
            await self._client.async_add_group_participants(group_jid, missing)
            roster = await self._client.async_get_group_participants(group_jid)
        if not self._hosts_present(roster, hosts):
            raise GuestGroupError("admin_add_unconfirmed")
        promote = [host for host in hosts if not self._host_is_admin(roster, host)]
        if promote:
            await self._client.async_promote_group_admins(group_jid, promote)
            roster = await self._client.async_get_group_participants(group_jid)
        if not all(self._host_is_admin(roster, host) for host in hosts):
            raise GuestGroupError("admin_promotion_unconfirmed")
        await self._client.async_set_group_security(group_jid, **SECURE_SETTINGS)
        await self._client.async_get_group(group_jid)
        security = await self._client.async_get_group_security(group_jid)
        roster = await self._client.async_get_group_participants(group_jid)
        if security != SECURE_SETTINGS:
            raise GuestGroupError("unsafe_group_settings")
        if not self._bot_is_admin(roster, account):
            raise GuestGroupError("bot_admin_unconfirmed")
        if self._unexpected_admin(roster, account, hosts):
            raise GuestGroupError("unexpected_group_admin")
        first_baseline = self.registry.snapshot()["observed_at"] is None
        await self._reconcile_roster(roster, account, security, ())
        await self.registry.async_set_provisioning_status("ready")
        if first_baseline:
            self._publish(self._reconciled_event())

    @staticmethod
    def _aliases(item: Any) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value.replace("@s.whatsapp.net", "@c.us")
                for value in (item.id, item.pn)
                if value
            )
        )

    def _hosts_present(self, roster: Iterable[Any], hosts: Iterable[str]) -> bool:
        active = {
            alias
            for item in roster
            if item.role != "left"
            for alias in self._aliases(item)
        }
        return all(host in active for host in hosts)

    def _host_is_admin(self, roster: Iterable[Any], host: str) -> bool:
        return any(
            host in self._aliases(item) and item.role in ("admin", "superadmin")
            for item in roster
        )

    def _bot_is_admin(self, roster: Iterable[Any], account: str) -> bool:
        return self._host_is_admin(roster, account.replace("@s.whatsapp.net", "@c.us"))

    def _unexpected_admin(
        self, roster: Iterable[Any], account: str, hosts: Iterable[str]
    ) -> bool:
        """Never trust group management by an unreviewed participant."""
        approved = {account.replace("@s.whatsapp.net", "@c.us"), *hosts}
        return any(
            item.role in ("admin", "superadmin")
            and not approved.intersection(self._aliases(item))
            for item in roster
        )

    def _roster_rows(
        self, roster: Iterable[Any], hosts: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        host_set = set(hosts)
        rows = []
        for item in roster:
            if item.role == "left":
                continue
            aliases = self._aliases(item)
            rows.append(
                {
                    "aliases": aliases,
                    "aliases_verified": item.pn is not None,
                    "kind": "host"
                    if any(alias in host_set for alias in aliases)
                    else "guest",
                    "role": item.role,
                }
            )
        return rows

    async def async_reconcile(
        self,
        *,
        event_hints: Iterable[Mapping[str, Any]] = (),
        force: bool = False,
    ) -> dict[str, Any]:
        """Read exact group and publish only persisted changes.

        A persisted ``suspended`` group becomes ready again only after fresh
        account, mapping, settings, bot role, host roles, and roster checks.
        This recovery performs no WhatsApp group writes.
        """
        if not self._enabled():
            self.registry.suspend()
            if self._events_available:
                self.failure_reason = "disabled"
            return self.snapshot()
        hints = tuple(event_hints)
        requested_at = time.monotonic()
        async with self._lock:
            now = time.monotonic()
            if not hints and self._last_refresh >= requested_at:
                return self.snapshot()
            if (
                not force
                and not hints
                and now - self._last_refresh < MIN_REFRESH_SECONDS
            ):
                return self.snapshot()
            self.registry.suspend()
            try:
                account = await self._prerequisites()
                if not self.registry.account_matches(account):
                    raise GuestGroupError("bot_account_changed")
                hosts = await self._reviewed_hosts()
                group_jid = self.registry.saved_group_jid
                if group_jid is None:
                    raise GuestGroupError("group_not_saved")
                await self._client.async_get_group(group_jid)
                security = await self._client.async_get_group_security(group_jid)
                roster = await self._client.async_get_group_participants(group_jid)
                if security != SECURE_SETTINGS:
                    raise GuestGroupError("unsafe_group_settings")
                if not self._bot_is_admin(roster, account):
                    raise GuestGroupError("bot_admin_unconfirmed")
                if not all(self._host_is_admin(roster, host) for host in hosts):
                    raise GuestGroupError("admin_role_drift")
                if self._unexpected_admin(roster, account, hosts):
                    raise GuestGroupError("unexpected_group_admin")
                if self.registry.snapshot()["provisioning_status"] == "suspended":
                    await self.registry.async_set_provisioning_status("ready")
                elif self.registry.snapshot()["provisioning_status"] != "ready":
                    raise GuestGroupError("group_not_ready")
                await self._reconcile_roster(roster, account, security, hints)
                self._last_refresh = now
                self.failure_reason = None
            except Exception as err:
                self.registry.suspend()
                self.failure_reason = (
                    err.reason
                    if isinstance(err, GuestGroupError)
                    else "guest_group_unavailable"
                )
                if self.registry.provisioning_status in ("ready", "suspended"):
                    await self.registry.async_set_provisioning_status("suspended")
            return self.snapshot()

    async def _reconcile_roster(
        self,
        roster: Iterable[Any],
        account: str,
        security: Mapping[str, bool],
        event_hints: Iterable[Mapping[str, Any]],
    ) -> None:
        hosts = await self._reviewed_hosts()
        changes = await self.registry.async_reconcile(
            self._roster_rows(roster, hosts),
            observed_at=time.time(),
            verified_bot_jid=account,
            security_verified=security == SECURE_SETTINGS,
            bot_admin_verified=self._bot_is_admin(roster, account),
            event_hints=event_hints,
        )
        omitted = False
        for change in changes:
            event = self._event(change)
            if event is None:
                omitted = True
            else:
                self._publish(event)
        if omitted:
            self._publish(self._reconciled_event())

    def _reconciled_event(self) -> dict[str, Any]:
        """Signal a snapshot query without inventing member join history."""
        snapshot = self.registry.snapshot()
        return {
            "schema_version": 1,
            "event_id": f"evt_{uuid4().hex}",
            "type": "group.membership.reconciled",
            "config_entry_id": self._entry.entry_id,
            "conversation_id": snapshot["conversation_id"],
            "conversation_type": "group",
            "occurred_at": None,
            "observed_at": self._iso(snapshot["observed_at"]),
            "source": "reconciliation",
            "group": {"group_id": snapshot["group_id"], "purpose": "guests"},
        }

    def _event(self, change: MembershipChange) -> dict[str, Any] | None:
        snapshot = self.registry.snapshot(membership_id=change.membership_id)
        if not snapshot["memberships"]:
            return None
        member = snapshot["memberships"][0]
        type_name = (
            "group.participant.role_changed"
            if change.type == "role_changed"
            else f"group.participant.{change.type}"
        )
        return {
            "schema_version": 1,
            "event_id": f"evt_{change.membership_id}_{snapshot['revision']}",
            "type": type_name,
            "config_entry_id": self._entry.entry_id,
            "conversation_id": snapshot["conversation_id"],
            "conversation_type": "group",
            "occurred_at": self._iso(change.occurred_at),
            "observed_at": self._iso(change.observed_at),
            "source": change.source,
            "group": {"group_id": snapshot["group_id"], "purpose": "guests"},
            "participant": {"participant_id": change.participant_id},
            "membership": member,
        }

    @staticmethod
    def _iso(timestamp: float | None) -> str | None:
        return (
            datetime.fromtimestamp(timestamp, UTC).isoformat()
            if timestamp is not None
            else None
        )

    def suspend(self, reason: str = "guest_group_unavailable") -> None:
        """Immediately disable guest-derived routing on external uncertainty."""
        self.registry.suspend()
        self.failure_reason = reason

    async def _verify_live(self, destination: str, *, private: bool) -> bool:
        """Fresh read-only send guard; never acquires the registry mutation lock."""
        if not self._enabled() or not self.registry.snapshot()["ready"]:
            return False
        try:
            account = await self._prerequisites()
            if not self.registry.account_matches(account):
                return False
            hosts = await self._reviewed_hosts()
            group_jid = self.registry.saved_group_jid
            if group_jid is None:
                return False
            await self._client.async_get_group(group_jid)
            security = await self._client.async_get_group_security(group_jid)
            roster = await self._client.async_get_group_participants(group_jid)
            if self._unexpected_admin(roster, account, hosts):
                self.suspend("unexpected_group_admin")
                return False
            if (
                security != SECURE_SETTINGS
                or not self._bot_is_admin(roster, account)
                or not all(self._host_is_admin(roster, host) for host in hosts)
            ):
                return False
            if not private:
                return destination == group_jid
            matches = [
                item
                for item in roster
                if item.role != "left" and destination in self._aliases(item)
            ]
            return len(matches) == 1 and destination != account
        except Exception:
            return False

    async def async_verify_guest_destination(self, destination: str) -> bool:
        """Confirm a private route's exact target before sending under registry lock."""
        return await self._verify_live(destination, private=True)

    async def async_verify_group_destination(self, destination: str) -> bool:
        """Confirm the managed group and its security before a group notify send."""
        return await self._verify_live(destination, private=False)

    async def async_send_to_group(self, text: str) -> Any:
        """Send one group text only after a fresh exact-destination verification."""
        destination = self.registry.saved_group_jid
        if (
            not isinstance(text, str)
            or not text.strip()
            or destination is None
            or not await self.async_verify_group_destination(destination)
        ):
            self.suspend("group_send_unavailable")
            raise GuestGroupError("group_send_unavailable")
        return await self._client.async_send_group_text(destination, text)

    async def async_handle_webhook(self, data: Mapping[str, Any]) -> bool:
        """Reconcile an already-authenticated change for the exact saved group.

        The upstream webhook handler must verify its signature and session.
        Join requests and unrelated groups do not trigger membership changes.
        """
        group_jid = self.registry.saved_group_jid
        session = self._client.session_name
        if not self._enabled() or group_jid is None:
            return False
        hint = participant_hint(data, session=session, group_jid=group_jid)
        if hint is not None:
            aliases = {
                alias.replace("@s.whatsapp.net", "@c.us")
                for pair in hint.participants
                for alias in pair
                if alias is not None
            }
            event_hints = (
                {
                    "alias": alias,
                    "type": hint.change_type,
                    "occurred_at": hint.occurred_at,
                }
                for alias in aliases
                if hint.occurred_at is not None
            )
            await self.async_reconcile(event_hints=event_hints, force=True)
            return True
        if is_managed_group_signal(data, session=session, group_jid=group_jid):
            await self.async_reconcile(force=True)
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """Return phone-free current readiness and membership information."""
        result = self.registry.snapshot()
        result["failure_reason"] = self.failure_reason
        return result
