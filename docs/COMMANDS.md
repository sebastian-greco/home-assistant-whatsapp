# Explicit WhatsApp commands

Status: included in integration v1.6.0, pending live Home Assistant/WhatsApp
verification. Earlier installed HACS versions do not have command support.

Commands are a small opt-in layer over the existing WAHA WhatsApp channel. An
incoming private text such as `/status` can produce a structured
`waha_whatsapp_event` with `type: command.requested`. The original
`message.received` event still fires. The integration never invokes an HA
service or AI agent because of message content; an explicitly configured
automation may consume the command request and decide what to do.

## Register a command

From an **active Home Assistant administrator** account, use **Developer
tools → Actions**:

```yaml
action: waha_whatsapp.register_command
data:
  config_entry_id: YOUR_WAHA_CONFIG_ENTRY_ID
  name: status
  aliases:
    - house
  allowed_contacts:
    - notify.waha_seba
  allow_current_guests: false
```

The `allowed_contacts` selector accepts individual WAHA notify entities from
this same connection. The integration stores their stable config-subentry IDs,
not their phone numbers or mutable entity IDs. No contact is allowed by
default. Set `allow_current_guests: true` only for commands every *currently
confirmed* guest may use. A guest request is accepted only in a private DM
while its managed-group membership is active; group messages never invoke
commands in this first version. You may leave `allowed_contacts` empty when
`allow_current_guests` is true.

Names and aliases use 1–32 ASCII letters, digits, or underscores, beginning
with a letter. Users type the leading slash; registration omits it. Invocation
is case-insensitive. A single-line message must start with the command, optionally
followed by up to eight shell-quoted arguments, each no longer than 80
characters. The entire command message must be at most 256 characters.
Unknown, malformed, unlisted-sender, group-chat, and media messages do not
produce a command request. They remain ordinary channel messages; the
integration sends no automatic rejection or help reply.

Registering an existing canonical name replaces its definition atomically.
Aliases cannot overlap another registered name or alias. Definitions persist
per WAHA config entry across Home Assistant restarts. If loading declarations
fails or their contents are invalid, command requests and changes are
disabled without disrupting ordinary notifications, polls, or inbound
messages; diagnostics reports `commands.available: false`. Home Assistant may
instead rename an unreadable JSON Store file and load an empty registry; in
that case no commands are active until an administrator registers them again.
Check the HA logs and restore from backup before changing a recovered Store.

To inspect or remove declarations, use the administrator-only actions
`waha_whatsapp.list_commands` (returns response data) and
`waha_whatsapp.unregister_command` with `config_entry_id` and the canonical
`name`. The list contains opaque contact subentry IDs, not phone numbers.

## Consume a request

For a registered `/status`, the event is shaped like this:

```yaml
event_type: waha_whatsapp_event
data:
  schema_version: 1
  event_id: evt_opaque_example
  type: command.requested
  config_entry_id: entry_example
  conversation_id: direct_opaque_example
  conversation_type: direct
  occurred_at: "2026-09-28T12:00:00+00:00"
  source_message_id: msg_opaque_example
  sender:
    contact_id: contact_opaque_example
    notify_entity_id: notify.waha_seba
    person_entity_id: person.seba
  command:
    name: status
    invoked_as: house
    arguments: []
```

The event has the same opaque conversation route and source event ID as the
accepted `message.received`. For a current guest, it also includes the
existing `sender.participant_id`, `group`, and `membership` metadata,
including the stay-specific `membership_id`. A guest has no fabricated
Person or HA user. The event does not expose a phone number or raw WhatsApp
JID. A Person-linked contact's `context.user_id` is attribution, **not**
authorization.

A deliberately harmless first consumer:

```yaml
alias: Reply to WhatsApp status command
triggers:
  - trigger: event
    event_type: waha_whatsapp_event
    event_data:
      type: command.requested
      config_entry_id: YOUR_WAHA_CONFIG_ENTRY_ID
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.command.name == 'status' }}"
actions:
  - action: waha_whatsapp.send_to_conversation
    data:
      conversation_id: "{{ trigger.event.data.conversation_id }}"
      message: "Casita is online."
mode: parallel
max: 10
```

An automation must explicitly handle arguments and decide whether to reply.
`send_to_conversation` revalidates a guest's current route before sending,
so a departed guest cannot be reached through an old stay handle. Consumers
performing non-idempotent work should persist `event_id` to avoid repeating
it after retries or automation restarts.

## First live test after release

1. In **Developer tools → Events**, listen for `waha_whatsapp_event`.
2. Register `/status` for your own individual WAHA notify entity using the
   action above. Leave `allow_current_guests: false` for this test.
3. Send `/status` to the WAHA number from that contact's WhatsApp account.
   Expect one `message.received` and one `command.requested` with matching
   `event_id` and `conversation_id`.
4. Send `status` without the slash. Expect `message.received` only. Repeat
   `/status` from an unlisted contact, or in the managed group; neither should
   produce `command.requested`.
5. Add the harmless reply automation above and send `/status` again. Confirm
   the reply arrives in the same private chat. Disable the automation and
   unregister the command when finished testing.

Do not use a lock, door, alarm, or PIN action for this first test. A missing
`command.requested` event should be investigated in integration diagnostics
and HA logs; there is no automatic WhatsApp error reply.

## Security boundary

This registry limits which *authenticated WAHA messages* generate a command
request. It is not a general HA authorization system: other HA code can fire
an event with the same name on the public event bus, and raw
`message.received` remains available to other consumers. Do not make a
door, lock, alarm, PIN, or similarly sensitive action depend only on
`command.requested`, `context.user_id`, or group membership. Such actions
need a separate execution endpoint with its own verified request correlation,
fresh membership/identity check, bounds, expiry, idempotency, and (where
appropriate) human confirmation. That is outside this first command phase.

Commands are private-chat only for now so an ordinary group discussion cannot
accidentally become a request and sensitive responses cannot leak into the
group. A future version can explicitly design group addressing and
per-command execution policy after the live guest-channel test.
