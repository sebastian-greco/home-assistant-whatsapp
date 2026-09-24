# WAHA WhatsApp integration v1.4.0 plan

Status: implemented for v1.4.0; HAOS/GOWS live validation remains pending.

The [channel design](WHATSAPP_CHANNEL_DESIGN.md) explains the longer-term
model. This document defines one practical first release: **receive a new
message from a configured contact and let a Home Assistant automation or
agent answer in the same direct chat**. Reactions are exposed as structured
events, but v1.4 does not assign meanings to emoji.

## Scope and user-facing contract

1. Preserve existing individual `notify` entities, direct sending, and poll
   actions with their current event behavior.
2. Extend the integration-owned, signed local webhook to subscribe to WAHA
   `message` and `message.reaction` alongside existing poll events. Do not
   overwrite unrelated user-configured WAHA webhooks.
3. Publish `waha_whatsapp_event` with `schema_version: 1`, `event_id`, `type`,
   `config_entry_id`, stable `conversation_id`, `conversation_type`, timestamp,
   sender identity metadata, and typed message/reaction details as specified
   in the design document. Supported v1.4 types: `message.received`,
   `reaction.added`, `reaction.removed`.
4. Accept unprompted direct messages from configured contacts. Resolve GOWS
   LID versus phone-number identity; ignore ambiguous, unknown, own, status,
   and group messages. A Person association is optional metadata.
5. Add `waha_whatsapp.send_to_conversation` with required `conversation_id`
   and `message`, optional `reply_to_message_id` for a WhatsApp quoted reply,
   and an optional response containing opaque `message_id` and
   `conversation_id`. It sends text only. Unknown or expired quote targets
   return a clear validation error; plain send remains available.
6. Represent incoming audio/image/file messages as typed metadata-only
   placeholders. Do not enable media download or create outbound media
   notifications in this release.
7. Publish examples for a safe `ping` → `pong` automation and a separately
   opt-in agent bridge. No inbound message automatically invokes Assist or
   controls a device.

The event and action names above are the implemented public API. Their schema
is covered by tests and docs so v1.4.x bug fixes do not force automation
rewrites.

## Implementation slices

### 1. Fixtures and schema

- Exercise synthetic fixtures for plain text, reply, reaction add/remove, LID
  sender, media placeholder, own message, unknown contact, and malformed
  payload. Compare them with redacted real events from the pinned WAHA/GOWS
  version during the pending HAOS trial.
- Define the event envelope and strict normalizer in separate modules. Keep
  WAHA-specific fields internal. Reserve version 1 of the schema; add new
  event `type` values later without changing existing fields.
- Distinguish a channel `conversation_id` from an HA conversation-agent ID.
  Store stable per-contact routing data across restarts and contact renames.

### 2. Inbound routing and identity

- Reuse the HMAC-verifying local webhook. Route poll events to the existing
  poll manager and message/reaction events to a new inbound manager. Validate
  session, event shape, sender and direct-chat destination before publishing.
- Resolve phone-number and `@lid` variants with WAHA's mapping where needed;
  fail closed when the mapped contact is not unique. A reply-to ID is optional
  because WAHA may omit it depending on the message/engine.
- Deduplicate webhook retries with a bounded, restart-tolerant cache keyed by
  WAHA event identity (with a documented fallback for older payloads). Expose
  an opaque, stable `event_id` so downstream consumers can also deduplicate.
- Apply limits to webhook body size, text length, and per-event processing.
  Emit counters/rejection reasons in diagnostics without content or numbers.

### 3. Outbound conversation action

- Resolve an opaque direct `conversation_id` only to a configured contact;
  never accept arbitrary WAHA chat IDs through the new action. Use the
  existing WAHA text API, extending it for optional `reply_to`.
- Preserve an opaque message-ID ↔ WAHA-ID mapping for bounded quoted replies
  and reaction correlation. Track outbound messages sent through `notify`
  entities and integration actions where they target a configured contact.
- Return a helpful error if the chat was removed, the session is unavailable,
  or a quoted message is no longer tracked. Sending unquoted text to a still
  configured conversation does not depend on a quote's retention window.

### 4. Documentation and real-device validation

- Document the event fields, action fields, privacy behavior, and YAML
  examples. Include one minimal automation that reacts only to `ping` from
  one configured contact and sends `pong` to the same `conversation_id`.
- Test a second, explicitly enabled example that passes text to
  `conversation.process`, retains that agent's returned conversation ID in a
  separate per-contact store, and sends the answer through
  `send_to_conversation`. This recipe must not imply that all configured
  contacts may control all Home Assistant devices.
- On the user's HAOS/GOWS installation, verify new text, a reply to an old
  notification, reaction add/remove, restart, and duplicate webhook delivery.
  Verify the preexisting sleep-mode poll still fires
  `mobile_app_notification_action` unchanged.

## Acceptance criteria

- A newly sent `ping` from `notify.waha_seba`'s configured WhatsApp contact
  produces one structured `message.received` event even when it is not a
  reply; the example automation can send `pong` back without a phone number
  or WAHA JID in YAML.
- A contact without a Person association also works; its event omits
  `person_entity_id`. A contact with an association has it, matching the
  existing notify state attribute. Neither event exposes the phone number.
- Reaction add/remove is distinguishable and tied to an opaque target message
  ID when available. No emoji automatically becomes a Home Assistant action.
- Unknown sender, group message, `fromMe` message, bad HMAC, wrong session,
  malformed payload, and ambiguous LID do not cause public events or replies.
- A WAHA retry does not normally cause duplicate automation execution, while
  documentation clearly states that the event bus is best effort and does
  not provide exactly-once or durable delivery. Home Assistant's storage API
  can log a failed disk write without raising it to this integration, so a
  restart after a storage failure may also lose deduplication/quote history.
- Existing poll tests and a real poll round-trip continue to pass.

## Deliberately later

- **Incoming voice transcription** (working label v1.4.5): selective audio
  retrieval, size/retention limits, language detection and transcription,
  then a `transcription.completed` event linked to the original message.
  No transcription provider is chosen by this plan.
- **Group destinations and guest membership events**: explicit group
  allowlisting, participant identity, group-scoped replies, and a separate
  access lifecycle. Group membership must not grant access by itself.
- **Commands and AI tool permissions**: a consumer interprets requests and
  authorizes actions. Especially for entry systems, require policy, audit,
  and likely confirmation; do not bake privileged commands into the
  transport.
- **HAOS sidebar work and outbound media notifications**: independent of
  this release, with outbound media not on the current priority path.

## Release approach

Publish the tested HACS integration as v1.4.0 so it can be installed for a
trial on the current HAOS app and its pinned GOWS engine. Run the
[HAOS verification checklist](V1_4_0_HA_TEST.md) after updating. If the
poll regression or text/reaction round-trips fail, issue a focused fix release.
This plan does not require a new HAOS app release unless the trial reveals an
app-level configuration or media limitation.
