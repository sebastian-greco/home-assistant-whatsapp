# Managed guest group implementation plan

Status: planned, not implemented. Updated: 2026-09-27.
Release version: to be assigned after implementation and verification.
Priority: next feature phase, before incoming voice transcription.

All event examples and new action names below are proposed contracts. They
cannot be used with the currently released integration. Final documentation
must replace proposals with the exact tested contract before release.

## Goal and responsibility boundary

Provide one opt-in, integration-managed WhatsApp guest group per config entry.
Home Assistant administrators with mapped WhatsApp contacts become its initial
human administrators. They add, approve, and remove guests using WhatsApp.

The integration manages communication and reports membership. It does not
create door PINs, grant house permissions, execute commands, choose an AI
agent, or interpret group membership as physical presence. Consumer automations
own those policies and their durable access records.

The guest setup is deliberately specific; participant identities, conversation
routing, and events are generic building blocks. Do not reintroduce the removed
family/adults/guests contact-grouping system or store preferences on Person
entities. This group is an actual WhatsApp chat, not a contact broadcast list.

## First-release scope

- Configure and provision one named guest group, initially disabled.
- Add/promote mapped HA administrators and secure group membership settings.
- Persist the exact group identity and resume setup safely after failure.
- Track confirmed participants and their membership lifecycle.
- Publish join, leave, and role-change events through `waha_whatsapp_event`.
- Send text notifications to the group through a native notify entity.
- Receive group messages/reactions with the actual participant identity.
- Receive private messages from confirmed current members and privately send
  text to them using opaque conversation handles.
- Expose a response-returning membership query for automation reconciliation.
- Preserve configured contacts, individual notify entities, existing direct
  conversation handles, Person metadata, and poll behavior.

Not included: arbitrary-group discovery/allowlisting UI, multiple guest groups
per entry, temporary Person/user/notify entities for each guest, group polls,
new dynamic-guest actionable polls, media downloads/transcription, access
provisioning, commands, or automatic acknowledgement messages. Existing
individual-contact polls remain supported.

## Configuration and provisioning

Configuration provides **Enable guest group**, a group name (suggested default
`Casita — Guests`), and a preview of mapped human administrators. Derive that
preview from configured recipient -> optional Person -> active HA administrator
user. An HA administrator without a unique WhatsApp mapping cannot be added;
evaluate eligibility per config entry, excluding inactive and system-generated
HA users. Do not borrow a recipient mapping from another integration entry.
show missing/ambiguous mappings and require correction or an explicit exclusion
before provisioning. Require at least one eligible human administrator.

Provisioning creates the group, saves its identity, adds/promotes the confirmed
administrators, and verifies: only admins can add members/edit group information;
invite-link joins require admin approval. Members may send messages. Casita's
WhatsApp account stays an admin so the integration can manage the group.
Setup must report participant-add/privacy failures and unsuccessful promotions.

Do not mark the group ready before all required steps are verified. A failed
step resumes against the saved group rather than creating another one. If a
create request times out with an unknown result, do not blindly retry creation
or adopt a group based on its display name; provide a recovery/adoption path
with explicit user confirmation. Creation is an external write and happens
only after the configuration confirmation, not as a diagnostic side effect.

Persist provisioning status separately from enabled/ready status. HA restarts,
renames, reloads, and normal upgrades must not create another group. Disabling
stops guest-derived routing and events, but does not delete the WhatsApp group
or remove people. Re-enabling reuses its identity after verification. Replacing
a group requires explicit confirmation and invalidates the old guest routes.

Bind the saved group, participant aliases, and guest routes privately to the
verified bot WhatsApp account identity, not just the WAHA session name. Confirm
that binding at startup/reconnect and before enabling group-derived routing.
Normalize account aliases only with verified mappings. A changed/unconfirmed
bot account suspends this feature; re-pairing the same session name to another
account must never silently reuse old identity/routing state. Recovery requires
reviewed adoption or replacement, without deleting the old WhatsApp group.

HA administrator discovery is a bootstrap/configuration operation, not an
unannounced continuous role synchronizer. Later HA admin changes require an
explicit reviewed update. Observe WhatsApp role/settings drift and report it;
do not silently promote/demote people. Loss of required bot privileges or
unsafe membership settings suspends the managed-group feature until resolved.
Do not claim that only HA admins can ever administer the WhatsApp group:
WhatsApp admins can change roles outside HA.

## Identity: account, membership, and destination are different

| Proposed field | Meaning | Lifecycle |
| --- | --- | --- |
| `participant_id` | Opaque identity for a resolved WhatsApp account in this integration entry | Stable across restart, display-name change, and verified PN/LID aliases while its registry record is retained |
| `membership_id` | One observed continuous membership of that account in this managed group | Stable across restart/reconciliation; a confirmed leave closes it, and a later join creates a new ID |
| `group_id` | Opaque identity of the exact managed WhatsApp group | Stable across rename/restart; changes when the group is replaced |
| `conversation_id` | Destination for the enclosing event: group chat or direct chat | Must never be used as a person's identity |
| `direct_conversation_id` | Private guest route scoped to the current membership | Valid only while that membership is confirmed and enabled; never becomes valid again on a later stay |
| `display_name` | Optional WhatsApp/contact display label | May change, be absent, duplicate another name, or contain untrusted content |

Use `participant_id`, not `guest_id`, as the generic identity field. A guest is
a participant with `membership.kind: guest`; initial mapped human hosts use
`kind: host`. WhatsApp admin role is separate from this classification: promotion
does not automatically turn a guest into a configured host or HA administrator.
Exclude the bot account from human guest onboarding.

Do not create HA Person entities or users for guests. Keep configured contacts'
optional Person association unchanged. If a member is also an existing contact,
attach its existing contact/Person metadata only after unambiguous identity
resolution, and preserve its independent direct-contact eligibility on removal.
For an existing configured contact's private message, publish one event using
its unchanged configured-contact destination; add membership metadata only if
temporally verified. No duplicate guest event. Group messages use group routing.
Host classification is not a house-action permission.

Persist opaque IDs and private routing/alias mappings in a versioned private
integration Store, not entity attributes or Person data. Public IDs must not
encode phone numbers. Resolve WhatsApp PN/LID aliases using verified WAHA
mappings; never merge accounts by profile name. Conflicting or unresolved
identities cannot obtain an actionable guest route.

An account identifier is not proof of a real-world person's identity. Number
reassignment, integration deletion, lost storage, or retention cleanup may
break continuity. No promise of a globally permanent identity. Names, public
IDs, and conversation handles are metadata, not secrets or permission tokens.

Active memberships are not evicted to meet a storage cap. Closed records use
bounded tombstone retention; determine and document limits during registry
implementation. Preserve closed IDs long enough for reconciliation and never
reuse them. If continuity cannot be proven, flag uncertainty instead of silently
associating a new stay with an old one. Consumer-owned PIN mappings must outlive
the integration's tombstones as needed; the integration never stores the PIN.

## Proposed public event contract

Keep `waha_whatsapp_event` and its versioned envelope. Existing v1.4 direct
events retain their contract. Treat the additions as additive schema-1 fields
only if implementation review confirms compatibility; otherwise explicitly
version the changed surface. New generic types:

- `group.participant.joined`
- `group.participant.left`
- `group.participant.role_changed`
- `group.membership.reconciled` (a signal to query the current snapshot)

Illustrative accepted-member event:

```yaml
event_type: waha_whatsapp_event
data:
  schema_version: 1
  event_id: evt_example_join
  type: group.participant.joined
  config_entry_id: entry_example
  conversation_id: group_conversation_example
  conversation_type: group
  occurred_at: "2026-09-27T12:00:00+00:00"
  observed_at: "2026-09-27T12:00:01+00:00"
  source: webhook
  group:
    group_id: group_example
    purpose: guests
  participant:
    participant_id: participant_example
    display_name: Maria # Optional, untrusted display label
    direct_conversation_id: guest_direct_stay_a
  membership:
    membership_id: membership_stay_a
    kind: guest
    status: active
    whatsapp_role: participant
```

The leave event contains the same participant and closed membership IDs, with
`status: left`. The old private handle may be included for correlation but is
already invalid for sending. Group-member events use `participant` for the
affected account, not `sender`. Omit an actor when WAHA cannot reliably identify
who made the change; never attribute a membership event's HA context to the
affected guest as though they initiated it.

Group messages retain `message.received` with `conversation_type: group` and
`sender.participant_id`, plus the sender's current membership and group metadata.
Guest private messages use `conversation_type: direct`, the membership-scoped
destination, and the same `sender.participant_id`/membership IDs. Include an
optional safe display label and existing contact metadata where available.
Bind guest membership metadata to the message/reaction occurrence time, not
merely the membership active when delivery arrives. A delayed event from stay A
must never acquire stay B's IDs or private route. Require a trustworthy timestamp
within a confirmed membership interval; reject guest-derived events when that
association is ambiguous. For a snapshot-discovered membership whose actual
start is unknown, do not attribute messages preceding its confirmation time.
Roles/status describe the observation; consumers still query current state
before delayed or sensitive work. Reactions use the same identity/time rules.

Each membership must have one clear authoritative representation on a message,
not inconsistent copies under sender/group. Finalize exact nesting in phase 1.
No raw WhatsApp JID/phone, media URL, webhook secret, complete WAHA payload, or
PIN is added to public events or diagnostics. Message text/display names can
appear in automation traces and must not be treated as trusted instructions.

## How a PIN automation identifies the correct guest

Illustrative consumer workflow, not a lock implementation:

1. On a confirmed guest join, check current membership and apply the consumer's
   explicit access policy. Key its durable record by
   `(config_entry_id, group_id, membership_id)`, storing `participant_id` as
   the account identity and the lock's credential/slot identifier separately.
2. Create one credential idempotently for that membership. Privately send the
   PIN using the event's `direct_conversation_id`, never the group destination.
   If the guest leaves before sending, the stale route fails instead of reaching
   a later stay. The consumer handles partial creation/send failures safely.
3. On leave, find that exact membership record and revoke that credential.
   Repeated leave events and retries must be harmless. A late leave from stay A
   must not revoke a new credential created for stay B.
4. On consumer/HA startup and periodically, reconcile stored credentials with
   the current confirmed membership snapshot. Handle unavailable snapshots
   using an explicit safety policy and independent credential expiry; absence
   of a webhook is never proof of ongoing membership.

No entity is required for any of these joins: the durable IDs are the keys.
Two participants called Maria get different IDs. Maria returning later can
retain her participant ID but gets a new membership ID and private route.

## Notifications, private routing, and membership query

Create one native group notify entity, for example `notify.waha_guests` (actual
entity ID remains subject to HA registry naming). It sends one message to the
group; it does not fan out private messages. Preserve the current notify
title/message rendering. Do not expose a participant roster or phones in state
attributes. Make readiness/unavailability clear.

Extend existing `waha_whatsapp.send_to_conversation` for verified group and
membership-scoped private handles. A join consumer uses the participant's
private handle; a reply consumer uses the incoming message's destination.
Continue enforcing quote-target ownership. Do not silently redirect a group
reply into a direct chat or change existing configured-contact handles.

Proposed response-returning action: `waha_whatsapp.get_group_membership`, scoped
to a config entry, returning opaque group identity, readiness, observation
time, snapshot revision, confirmation/freshness status, and active memberships.
Include bounded closed membership records when available. An empty confirmed
snapshot is different from unknown/unavailable. Support targeted membership
lookup so consumers can distinguish active, left, and unknown/expired records.
Do not expose phones or internal mappings. Finalize action/schema in phase 1.

Group-derived routing requires confirmed current membership; restored state
alone is insufficient. Verify membership before private sends, serialize this
check with local leave processing, and deny on failed/ambiguous verification.
An external removal racing a send cannot be made perfectly atomic; document
that limit. Unknown senders and unrelated groups remain ignored. Eligibility
to communicate is not authorization to execute a house command.

The existing raw `send_message(to=...)` action remains an independently
authorized outbound HA action, not subject to the guest-route policy. Clearly
document this distinction so consumers do not bypass membership checking by
falling back to a saved phone number.

## Membership reconciliation, ordering, and storage

Signed webhook/session validation and bounded deduplication remain mandatory.
Consume participant join/leave/promote/demote payloads for the exact saved
group. Do not confuse events about the bot joining/leaving with an individual
guest change. Join requests are not active membership.

Refresh the authoritative roster at startup/reconnect, following changes, and
on bounded periodic reconciliation. Reject guest routing while roster readiness
or required group-security settings are unconfirmed. Rate-limit refreshes and
coalesce concurrent lookups; choose/document intervals after installed-version
testing. Never apply old/out-of-order participant events blindly over newer
confirmed state. Persist membership transitions before publishing events.

First setup provides a snapshot/reconciled signal for existing members, not
invented historical joins. Later snapshot differences emit reconciled changes
with `source: reconciliation`; unknown occurrence time is null, with a real
`observed_at`. Do not apply direct-message freshness filtering in a way that
silently ignores membership revocation. Preserve IDs for unchanged confirmed
memberships. A leave/rejoin entirely during downtime may be unobservable:
snapshots cannot prove uninterrupted presence, so document this limit and keep
credential expiry/access policy in the consumer.

Events are best effort, not an exactly-once queue. A save/event failure must
not restore guest eligibility after a known leave. Unknown or corrupt membership
storage disables guest routing until recovery; do not use volatile fallback to
claim durable guest identity. Keep configured contacts operational independently.
No credentials, phone lists, message content, or profile names in diagnostics;
expose redacted readiness/sync times, counts, and failure reasons only.

## Implementation phases and quality gates

1. **Installed-WAHA capability spike and contract freeze.** Verify the pinned
   GOWS version supports creation, role promotion, security settings, roster
   retrieval, PN/LID mapping, and participant events. Record sanitized fixtures.
   Verify the session account identity used for the private bot binding.
   Resolve exact public schema, query action, retention, and bootstrap UX.
2. **Private identity/membership registry.** Versioned Store, participant aliases,
   membership IDs/closures, invalidated routes, snapshots, ordering, restart
   recovery, deduplication, and corrupt-storage behavior.
3. **Opt-in setup and provisioning.** Config/options flow, administrator preview,
   readiness, resumable provisioning, group reuse, explicit recovery, and drift
   handling. Never create a group during a read-only capability check.
4. **Communication and events.** Group notify entity, participant webhooks,
   group/private inbound messages and reactions, private routing, and membership
   query. Preserve direct-contact and poll compatibility.
5. **Tests and review.** Unit/fixture tests plus a fresh real HA/WhatsApp trial;
   verify no phones leak into public surfaces. Fix defects before release.
6. **Final documentation and release.** Write the shipped user/API references
   below, validate examples, update README/changelog, and only then assign and
   publish the release. This plan does not authorize an automatic release.

Required test cases: duplicate-enable/restart; partial setup and unknown create
result; missing/duplicate admin mappings; inactive/system-generated admin users
and cross-entry mapping isolation; add blocked by WhatsApp privacy; loss
of bot privileges/settings drift; batched participant changes; PN/LID aliases
and conflicts; same name/different people; renamed/rejoining guest; stale leave
from an earlier stay; delayed messages/reactions crossing a leave/rejoin;
missing/untrustworthy timestamps; delayed/out-of-order events; approval requests; bot/host
filtering; unrelated-group/unknown-sender/bad-HMAC rejection; private send racing
removal; restart/offline removal and snapshot reconciliation; storage failure;
same-session re-pairing to another bot account and unconfirmed account identity;
existing permanent contact surviving removal; quote ownership; clean event
metadata; all current notify/poll/channel regressions.

Live verification uses a test participant and harmless messages, not a real
door PIN. Confirm admin-only additions and approval, join/leave identities,
group notification, guest DM/reply, removal rejection, rejoin with a new
membership ID, and restart stability. Record unsupported pinned-version
features before proposing any HAOS app update.

## Release documentation deliverables

- `docs/GUEST_GROUPS.md`: enable/setup/admin mappings; adding/removing guests;
  group notifications; private conversations; disable/recovery/availability;
  identities, optional names, and limits. Clearly distinguish current features
  from future command/access workflows.
- `docs/GUEST_GROUP_API.md`: exact event fields/types, sender vs participant,
  host/guest vs WhatsApp roles, nullable/omitted fields, ID/route lifecycles,
  membership-query and send action schemas, snapshot freshness/unknown states,
  ordering/deduplication, and privacy. Include full example payloads so people
  and AI agents do not need to infer the interface from implementation code.
- `docs/GUEST_GROUP_AUTOMATIONS.md`: tested HA examples for group welcome,
  private welcome, filtering a particular participant/membership, messages,
  and membership reconciliation. Explain durable membership-to-credential
  mapping, idempotent removal, per-stay expiry, and partial failure without
  inventing a universal lock action or silently granting access.
- `docs/GUEST_GROUP_HA_TEST.md`: reproducible live checklist and redacted
  troubleshooting, including remove/rejoin and restart/offline scenarios.

Update the root README and channel architecture to link these references and
describe the shipped boundary. Runtime/API documentation is a release gate,
not deferred cleanup. Do not present this design document as a working recipe.

## Primary references and verification caveat

[WAHA groups](https://waha.devlike.pro/docs/how-to/groups/) documents creation,
admin promotion, membership security settings, and GOWS participant-change
events. Its `group.v2.participants` event describes changed participants and
join/leave/promote/demote types; `group.v2.join`/`leave` concern the session
account itself. Current docs may exceed the pinned engine version. Capability
testing, not a feature-table assumption, is the first implementation gate.

Use the existing [channel architecture](../WHATSAPP_CHANNEL_DESIGN.md) and
current `channel_registry.py`, `inbound.py`, `services.yaml`, config flow,
notify, and poll tests as compatibility constraints. No live HA configuration
or WhatsApp group was changed when recording this plan.
