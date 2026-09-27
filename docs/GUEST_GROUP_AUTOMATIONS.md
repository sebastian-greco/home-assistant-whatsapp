# Managed guest-group automations

This guide covers Home Assistant automation patterns for the managed group.
The integration transports WhatsApp messages and confirms membership; an
automation must opt in to every action. Examples below describe the current
source contract, but have not yet been validated in a fresh live HA/WhatsApp
installation. Check the installed release and the
[live test checklist](GUEST_GROUP_HA_TEST.md) first.

Replace sample entity and membership IDs with values from your installation.
For service actions, use the UI selectors where possible. `config_entry_id`
identifies the WAHA integration entry; it is not a WhatsApp identity. See the
[API reference](GUEST_GROUP_API.md) for response fields and lifecycle rules.

## Send a group welcome notification

Use the group's notify entity for a message intended for everyone in the
WhatsApp group. It is not a private guest message.

```yaml
alias: Guest group arrival information
triggers:
  - trigger: state
    entity_id: input_boolean.guest_arrival_notice_pending
    to: "on"
actions:
  - action: waha_whatsapp.get_group_membership
    data:
      config_entry_id: YOUR_CONFIG_ENTRY_ID
    response_variable: guest_group
  - if:
      - condition: template
        value_template: "{{ guest_group.ready and guest_group.confirmed }}"
    then:
      - action: notify.send_message
        target:
          entity_id: notify.waha_guests
        data:
          title: Arrival information
          message: The Wi-Fi details are in the welcome folder.
      - action: input_boolean.turn_off
        target:
          entity_id: input_boolean.guest_arrival_notice_pending
```

Availability is only a UI-level readiness hint. Sends still perform a fresh
verification and can fail if the group or required settings changed between
the condition and action. Decide how your automation should handle a failed
send; do not silently send the same text to individual members. The pending
helper remains on if the action sequence fails before the final turn-off, so
you can correct the issue and retry deliberately.

## Check a membership before a private welcome

Membership events are emitted only after a roster transition is confirmed and
saved. For a side effect that should happen only while the membership is still
active, query the event's exact `membership_id` before sending. The action
performs a fresh, read-only WAHA roster/security reconciliation; it does not
write to WhatsApp. If that check discovers a missed transition, it can publish
the resulting membership event before returning. Compare readiness and
observation time with your own freshness policy.

```yaml
alias: Welcome a new WhatsApp guest privately
triggers:
  - trigger: event
    event_type: waha_whatsapp_event
    event_data:
      type: group.participant.joined
conditions:
  - condition: template
    value_template: >-
      {{ trigger.event.data.group.purpose == 'guests'
         and trigger.event.data.membership.kind == 'guest'
         and trigger.event.data.membership.status == 'active' }}
actions:
  - action: waha_whatsapp.get_group_membership
    data:
      config_entry_id: "{{ trigger.event.data.config_entry_id }}"
      membership_id: "{{ trigger.event.data.membership.membership_id }}"
    response_variable: guest_membership
  - if:
      - condition: template
        value_template: >-
          {{ guest_membership.ready
             and guest_membership.confirmed
             and guest_membership.membership_found
             and guest_membership.memberships | count == 1
             and guest_membership.memberships[0].status == 'active'
             and guest_membership.memberships[0].membership_id
                 == trigger.event.data.membership.membership_id }}
    then:
      - action: waha_whatsapp.send_to_conversation
        data:
          conversation_id: >-
            {{ guest_membership.memberships[0].direct_conversation_id }}
          message: >-
            Welcome. The group is for general updates; message me here for a
            private reply.
```

This example sends whenever the automation runs. Add a durable `event_id`
deduplication guard before using it in a workflow with retries or manual
re-runs.

The fresh route validation on `send_to_conversation` is the final guard. It
rejects the route if the member has already left, the bot/account/settings are
not verified, or the private destination cannot be uniquely resolved.
Handle action errors according to your workflow rather than retrying against a
different destination.

## Filter a known person or one specific stay

Use an observed `participant_id` to associate an account across stays, or a
`membership_id` to refer to only one stay. For a particular stay, compare the
membership ID on *both* join and leave events. Do not identify people by
display name or by the order of entries in `memberships`.

```yaml
conditions:
  - condition: template
    value_template: >-
      {{ trigger.event.data.participant.participant_id
           == 'participant_REPLACE_WITH_OBSERVED_ID'
         and trigger.event.data.membership.membership_id
           == 'membership_REPLACE_WITH_OBSERVED_ID' }}
```

The sample IDs above are placeholders; real IDs are opaque generated values.
Persist the IDs in your own durable integration/automation data if you need
them after a Home Assistant restart. Do not derive an identity from WhatsApp
names, phone-number fragments, or the random-looking text in an ID.

## Handle member messages without turning them into commands

Group messages use the group's `conversation_id`; a reply there is visible to
the whole group. A private member message uses a membership-scoped direct
`conversation_id`; replying to that value stays private. The event's
`sender.participant_id` and `membership.membership_id` let a consumer filter
messages without a guest entity.

This example replies only to one exact harmless phrase in the managed group:

```yaml
alias: Reply to a guest-group status question
triggers:
  - trigger: event
    event_type: waha_whatsapp_event
    event_data:
      type: message.received
conditions:
  - condition: template
    value_template: >-
      {{ trigger.event.data.conversation_type == 'group'
         and trigger.event.data.group.purpose == 'guests'
         and trigger.event.data.message.kind == 'text'
         and trigger.event.data.message.text | trim | lower == 'wifi?' }}
actions:
  - action: waha_whatsapp.send_to_conversation
    data:
      conversation_id: "{{ trigger.event.data.conversation_id }}"
      message: >-
        The network name and password are on the card in the welcome folder.
```

For a private DM response, use the incoming event's `conversation_id` instead.
Do not route group conversation text into a direct conversation or vice versa.
Incoming text is untrusted and may be sensitive; it can appear in event
listeners and traces. This integration does not execute services based on
message content. Avoid broad “run this text as an Assist command” automations
unless you have separately designed authentication, allowlists, and a narrow
authorization boundary.

## Reconcile consumer-owned records

`group.membership.reconciled` means that a snapshot was established or a
reconciliation found changes that cannot be represented as reliable individual
transitions. It is a prompt to call `get_group_membership`, not a join event.
The first provisioning snapshot is deliberately a baseline and does not invent
historical join times. Query the whole snapshot (omit `membership_id`) and
reconcile consumer records against active membership IDs. A query refreshes
the WAHA roster and may itself publish events for changes it discovers; it
does not make external WhatsApp group changes. Never treat an unconfirmed or
unavailable response as an empty confirmed group. A targeted query includes
`membership_found`: `true` means the active or retained closed membership was
found; `false` means the ID is unknown or its closed record has expired from
bounded retention.

If another integration manages credentials, access slots, or tasks, keep its
records keyed by `(config_entry_id, group_id, membership_id)` and store the
lock's own credential/slot ID separately. Use `participant_id` only as an
account-level association. On a confirmed leave, update or revoke only the
record for that exact membership. Retrying the same event must be idempotent;
a late leave from an older stay must never revoke a later stay's credential.
Persist consumer state before doing external side effects where possible and
define recovery behavior for partial success.

Home Assistant events are best effort and not an exactly-once queue. Use
`event_id` for bounded deduplication, keep durable consumer state, reconcile
after restart, and give any external authorization an independent expiry. A
leave and rejoin that both happen while HA/WAHA is offline may be invisible to
the snapshot comparison; current code cannot prove that continuous membership
was interrupted in that interval. If your policy cannot safely tolerate this
uncertainty, do not grant long-lived access from group membership.

## PINs, locks, and access control are not provided

The integration has no PIN generation/storage, lock service, access-granting
policy, guest-user or Person entity, or physical-presence inference. Do not
send a PIN in the group: group members can see it. If a separately reviewed
consumer automation issues a credential, it must send it only over the
membership's private route, revoke only the matching stay's credential on
leave, and handle the case where the guest leaves between credential creation
and message delivery. The integration itself does none of those operations.
