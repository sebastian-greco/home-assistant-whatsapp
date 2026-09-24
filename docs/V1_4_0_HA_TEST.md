# Verify WAHA WhatsApp v1.4.x on Home Assistant OS

This is a deliberately harmless end-to-end test of the HACS integration on the
existing WAHA HAOS app. It does not require a new app version or expose WAHA's
API port. Use a configured individual contact, and do not connect an AI agent,
door automation, or other privileged command while testing.

## Before testing

1. In HACS, update **WAHA WhatsApp** to the latest **1.4.x**, then restart Home Assistant.
   Keep the existing WAHA app installed and its WhatsApp session in `WORKING`
   state. HACS updates the integration, not the HAOS app.
2. In **Settings → Devices & services**, confirm the WAHA WhatsApp integration
   and the contact's `notify.waha_*` entity are available. Send one ordinary
   notification first; it should still arrive.
3. Open **Developer tools → Events**, enter `waha_whatsapp_event` under
   **Listen to events**, and start listening. This tool shows message content,
   so do not use a password or private information as test text.

## Direct-message round trip

1. From the configured contact's WhatsApp account, send a *new* message to
   the WAHA number: `WAHA 1.4 test ping`. Do not reply to an older notification.
2. Expect one `waha_whatsapp_event` with `type: message.received`,
   `message.kind: text`, and your text. Record the event's opaque
   `conversation_id` and `message.id`. `sender.notify_entity_id` should identify
   the individual contact; `sender.person_entity_id` appears only if linked.
   There should be no phone number, WAHA chat ID, or media URL in the event.
3. In **Developer tools → Actions**, run the following, replacing the two
   placeholders with the values from the event. The first send tests normal
   routing; the second tests a WhatsApp quoted reply.

   ```yaml
   action: waha_whatsapp.send_to_conversation
   data:
     conversation_id: "PASTE_CONVERSATION_ID"
     message: "WAHA 1.4 pong"
   ```

   ```yaml
   action: waha_whatsapp.send_to_conversation
   data:
     conversation_id: "PASTE_CONVERSATION_ID"
     reply_to_message_id: "PASTE_MESSAGE_ID"
     message: "WAHA 1.4 quoted pong"
   ```

4. Confirm both arrive in the same direct chat and that the second quotes the
   original ping. An unknown or stale `conversation_id` must fail rather than
   send to an arbitrary number.

## Reactions and poll regression

1. React with 👍 to the new `WAHA 1.4 pong` message. Expect
   `type: reaction.added`, `reaction.emoji: 👍`, and
   `reaction.target_known: true`. Remove the reaction and expect
   `type: reaction.removed` with an empty emoji. The integration must not
   perform any action solely because of a reaction.
2. In **Developer tools → Events**, listen for both
   `mobile_app_notification_action` and `waha_whatsapp_event` (use separate
   browser tabs if needed). After installing 1.4.1 and restarting Home
   Assistant, send this **new** test poll from
   **Developer tools → Actions**, replacing the notify entity if needed:

   ```yaml
   action: waha_whatsapp.send_poll
   data:
     entity_id: notify.waha_seba
     title: "WAHA 1.4.1 poll test"
     message: "This test changes nothing in the house."
     actions:
       - action: TEST_WAHA_V141
         title: Confirm test
     no_action_title: No action
     settle_seconds: 5
   ```

3. Choose **Confirm test** and leave it selected for at least five seconds.
   Expect one `mobile_app_notification_action` with
   `data.action: TEST_WAHA_V141` and, on 1.4.1 or later, one
   `waha_whatsapp_event` with `type: poll.selection_settled`,
   `poll.selected_option: Confirm test`, and
   `poll.action_id: TEST_WAHA_V141`. This checks both the old action contract
   and the unified channel. No automation should be attached to this test
   action. On another new test poll, choosing **No action** should produce
   only the settled channel event, with `poll.action_id: null`.

## Optional safety and troubleshooting checks

- If another, unconfigured WhatsApp number is available, send a harmless test
  message from it. It must not produce `waha_whatsapp_event`.
- Run the [README ping automation](../README.md#receive-a-whatsapp-message)
  only after the manual round trip succeeds. It demonstrates automatic
  replies without enabling general commands.
- If an event is missing, download the integration's diagnostics and inspect
  `inbound_channel.rejection_reasons`, `actionable_polls.rejection_reasons`,
  and `persistence_available`. If a vote on a **new** poll increments
  `unknown_poll`, the vote reached Home Assistant but did not match an
  outstanding poll; record the test time for investigation. Confirm the
  WAHA session is `WORKING`, the contact is configured, and both machines'
  clocks are correct. Inbound message/reaction events over one hour old or
  more than five minutes in the future are discarded.
- When reporting a failure, include the HACS integration version, WAHA app
  version, test step, UTC time, redacted rejection counts, and whether ordinary
  notifications and polls still work. Do **not** share API keys, webhook
  secrets, phone numbers, or full message payloads.

Home Assistant's event bus and WAHA webhook retries are best effort; this
checklist does not prove durable or exactly-once delivery. Keep your existing
notification path enabled while evaluating the new inbound channel.
