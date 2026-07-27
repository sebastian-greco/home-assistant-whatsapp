# Migrate Home Assistant notifications to WAHA WhatsApp

This guide covers ordinary and poll-based actionable WAHA WhatsApp
notifications. It keeps existing automation action handlers stable,
introduces WhatsApp gradually, and preserves the Home Assistant Companion App
where WhatsApp cannot replace its behavior.

## Before changing automations

1. Confirm the WAHA app session reports `WORKING`.
2. Add each recipient under **Settings → Devices & services → WAHA WhatsApp**.
3. Confirm the expected notify entities exist under **Settings → Devices &
   services → Entities**. Typical IDs are:

   - `notify.waha_seba`
   - `notify.waha_lucila`

4. Send one direct test from **Developer Tools → Actions → YAML mode**:

   ```yaml
   action: notify.send_message
   target:
     entity_id: notify.waha_seba
   data:
     title: "WhatsApp test"
     message: "Home Assistant can send through WAHA."
   ```

Home Assistant keeps entity IDs in its entity registry. If an entity was
renamed previously or its preferred ID was already occupied, use the ID shown
by the entity picker instead of assuming the examples above.

## What maps cleanly

The native `notify.send_message` action accepts the two fields used by most
notification wrappers:

| Existing field | WAHA behavior |
| --- | --- |
| `title` | Rendered as a bold WhatsApp heading. |
| `message` | Rendered below the title as ordinary text. |
| logical recipient | Mapped to an individual contact notify entity. |

The integration does not infer recipient sets from Home Assistant People or
store roles, notification preferences, or memberships on Person entities.
Route to explicit individual targets in your central notification script.
Associated contacts expose `person_entity_id` on their individual notify
entity. The phone number is not exposed.

## Actionable notifications

The Companion App publishes a button's action ID on Home Assistant's
`mobile_app_notification_action` event. WAHA WhatsApp polls publish the same
event after a choice has remained stable for five seconds. Existing event
triggers therefore remain unchanged.

The outbound delivery call is channel-specific: continue using
`notify.send_message` for the Companion App, and use
`waha_whatsapp.send_poll` for the WhatsApp branch when `data.actions` is not
empty.

```yaml
- action: waha_whatsapp.send_poll
  continue_on_error: true
  data:
    entity_id: "{{ whatsapp_target }}"
    title: "{{ final_title }}"
    message: "{{ message }}"
    actions: "{{ data.actions }}"
    settle_seconds: 5
```

Only the existing `action` and `title` fields are used; harmless mobile-only
fields are ignored. Text-input/`REPLY` and `URI` actions are rejected because
polls cannot preserve their semantics. A notification containing one real
action gets a second, non-triggering option because WhatsApp polls need at
least two choices. For example, `Cancel` plus `Keep scheduled` publishes
`CANCEL_SLEEP_MODE` only when `Cancel` is selected.

The integration correlates the authenticated vote with the outgoing poll,
waits for quick corrections, and fires the event once. It does not perform the
action, create an expiry timer, or send a confirmation. Keep all workflow
conditions, timers, service calls, and follow-up messages in the existing
action-handler automation. Add a state guard there if an old poll choice must
be ignored after the workflow changes.

For an individual WhatsApp contact associated with a Home Assistant Person,
the event uses that Person's currently linked active user as
`trigger.event.context.user_id`. No user ID is persisted or placed in event
data; contacts without a linked user continue to produce a null user context.
The event origin is `REMOTE`, matching Companion App webhook actions.

WAHA webhooks are configured automatically. With the companion HAOS app the
callback stays on the private app network and requires a SHA-512 HMAC; no
public URL or manual webhook configuration is needed. For an external WAHA
server, the Home Assistant internal URL must be reachable from that server.

If a poll vote cannot be decrypted or fails correlation, no event is fired and
no automatic retry or confirmation is sent. Check Home Assistant logs and the
integration's diagnostics for sanitized failure and rejection-reason counters;
they contain no phone numbers, message IDs, or poll contents. Direct votes from
GOWS are compatible when WAHA changes the sent `@c.us` message-ID envelope and
webhook identities to `@lid`: the stable message token is correlated and WAHA's
LID mapping must resolve both identities to the configured phone-number chat.

## What still does not map

The integration still does not implement:

- free-form replies and text commands;
- Companion App tags, replacement, clearing, persistence, or sticky behavior;
- Android notification channels, TTL, importance, or iOS interruption levels;
- delivery acknowledgement, retries, or automatic fallback;
- inbound commands or identity verification.

Keep Alarmo and other safety-critical notifications on the Companion App as a
tested fallback even if they are also copied to WhatsApp. A mobile
`clear_notification` command is an app operation and must not be converted to
WhatsApp text.

## Recommended migration: preserve the central router

Do not replace every automation individually when existing automations already
call recipient wrapper scripts. Add WhatsApp behind the central notification
router so its callers continue to pass the same `person`, `title`, `message`,
`priority`, and `data` fields.

Add a target map and WAHA action after the router has constructed its final
title. The following block assumes the router already defines `person`,
`final_title`, and `message`:

```yaml
- variables:
    whatsapp_targets:
      sebastian: notify.waha_seba
      lucila: notify.waha_lucila
    whatsapp_target: "{{ whatsapp_targets.get(person) }}"

- if:
    - condition: template
      value_template: "{{ whatsapp_target is string }}"
  then:
    - action: notify.send_message
      continue_on_error: true
      target:
        entity_id: "{{ whatsapp_target }}"
      data:
        title: "{{ final_title }}"
        message: "{{ message }}"
```

`continue_on_error: true` prevents a temporary WAHA or WhatsApp failure from
stopping the remainder of a routine. It does not retry the message.

### Discover a contact from its Person association

To avoid maintaining a name-to-notify-entity map, a router that already
receives a Person entity ID can discover the associated individual WAHA notify
entity:

```yaml
- variables:
    whatsapp_target: >-
      {{ states.notify
         | selectattr('attributes.person_entity_id', 'eq', person_entity_id)
         | map(attribute='entity_id')
         | first
         | default(none) }}

- if:
    - condition: template
      value_template: "{{ whatsapp_target is string }}"
  then:
    - action: notify.send_message
      continue_on_error: true
      target:
        entity_id: "{{ whatsapp_target }}"
      data:
        title: "{{ final_title }}"
        message: "{{ message }}"
```

For a known notify entity, the same metadata is available directly through
`state_attr('notify.waha_seba', 'person_entity_id')`. The attribute is absent
when a contact has no Person association, so routers must handle no match.

Keep logical priority in the router even though WhatsApp has no equivalent to
mobile notification channels. The router can continue expressing urgency in
`final_title`, for example by adding `🚨` for urgent messages and `💬` for low
priority messages.

## Rollout phases

### 1. Test only

Send manually to each configured individual contact and confirm the expected
person receives it.

### 2. Dual delivery

Keep the existing Companion App action and add the WAHA action after it in the
central router. Run both channels for ordinary messages while checking message
formatting, recipient routing, and WAHA availability.

### 3. WhatsApp-first informational messages

After a stable trial, remove Companion App delivery only for ordinary
informational messages. Retain the existing wrapper script interfaces so the
choice can be reversed without editing every automation.

### 4. Add actionable polls with a critical fallback

Use `waha_whatsapp.send_poll` for actionable WhatsApp branches while keeping
the existing Companion App action notification during rollout. Continue dual
delivery for Alarmo, security, access, power outage, and other safety-sensitive
workflows until their state guards and fallback behavior have been tested.

## Troubleshooting

### A recipient exists but its notify entity does not

Version `1.0.1` and newer reload automatically after recipient changes. On
`1.0.0`, use the integration's three-dot menu and select **Reload**, then update
through HACS. If the problem remains on a newer version, inspect
**Settings → System → Logs** for `waha_whatsapp` errors.

### Sending fails

Verify that the WAHA session is `WORKING`, the HAOS app is running, and the
integration is loaded. Keep port 3000 private; the companion integration uses
Home Assistant's internal app network and does not require public exposure.

### The entity ID differs from this guide

Use the entity picker as the source of truth. Home Assistant preserves user
renames and may add a numeric suffix to avoid a collision. Changing an entity
ID later requires updating every automation, script, scene, and dashboard that
references it.

## Upgrading from household groups

Version `1.1.0` removes the former Family, Adults, and Guests configuration and
fan-out entities. During config-entry migration, the integration:

- preserves every recipient subentry, phone number, contact title, optional
  Person association, and individual notify entity;
- removes only the obsolete role/adult flags from integration-owned config
  data; and
- removes only the three integration-owned legacy group entities from the
  entity registry.

Update automations that referenced the removed group entities to target
individual contacts explicitly. A future generic “broadcast to all configured
contacts” would be a separate opt-in feature, not a household role or group.
