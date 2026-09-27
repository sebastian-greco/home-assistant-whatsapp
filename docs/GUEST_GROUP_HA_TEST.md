# Live Home Assistant test: managed guest group

This checklist is intentionally manual. It does not run a script, test suite,
or automation that creates a live WhatsApp group, adds/removes a member, or
sends a group message. The explicit final confirmation in the integration
options flow is a real external write. Perform it only when an HA
administrator deliberately chooses to create a test group and has read the
previewed recipients. Never use a household production group for first
verification.

Status: included in integration version 1.5.0, but no live HA/WhatsApp trial
has yet been performed. Completing this checklist is a rollout gate, not a
claim that testing already succeeded. Unit tests do not replace this trial.

## Before the first write

1. Use a test Home Assistant instance if available and a dedicated test WAHA
   WhatsApp account, not an account containing private household chats. Make
   a cold backup of the WAHA app data before any WAHA engine upgrade. Do not
   log out, clear the session store, or replace an existing bot identity to
   prepare for this test.
2. Confirm the WAHA session is `WORKING`, uses GOWS, and reports version
   2026.8.2 or newer. The source-tree HAOS app is currently pinned to
   2026.9.1; external WAHA installations must be checked independently.
   The integration rejects lower versions because required membership
   security controls are unavailable there.
3. Confirm the installed HACS integration is version 1.5.0 or newer and
   contains managed guest groups. The HAOS app has its own version; its
   `waha-v...` tag is not the HACS integration version.
4. Prepare at least one test Home Assistant administrator mapped to one
   `person.*` entity and one WhatsApp recipient in this WAHA entry. Review all
   other admins and explicitly exclude unmapped/ambiguous ones or fix their
   mapping. The configuration preview must match what you intend to add.
5. Make sure a second test WhatsApp account is available as a guest. Choose
   harmless test text only; do not use a real PIN, password, or private
   message. Do not attach an automation or AI agent that executes actions.
6. In **Developer tools → Events**, listen for `waha_whatsapp_event` before
   continuing. Event data may include the text you send, so keep it
   non-sensitive.

## Provision once, by explicit UI confirmation

1. Open **Settings → Devices & services → WAHA WhatsApp → Configure**. Enable
   the managed guest group and enter an unmistakable test name. Review the
   mapped and excluded administrators on the first form.
2. Submit and read the separate confirmation form. It may create one actual
   WhatsApp group, add/promote the mapped admins, and apply group settings
   after the entry reloads. Submit this final confirmation manually only if
   those writes are intended. No automation or script should submit this for
   you.
3. Wait for the integration entry to reload. Confirm the group's notify
   entity (normally `notify.waha_guests`) becomes available and diagnostics
   show `managed_guest_group.ready: true` with an observed roster time. If it
   remains unavailable, stop and inspect the safe failure reason; do not
   repeatedly enable/disable to trigger another create.
4. In WhatsApp, inspect the new test group's settings. Confirm only admins
   can add participants or edit group information, regular members may send
   messages, and invite-link joins require admin approval. Confirm the WAHA
   account and mapped admins are admins, with no unreviewed account promoted
   as an admin. Do not continue if any required setting is missing or cannot
   be read back.

If provisioning returns an unknown creation outcome, do not retry group
creation, search by display name and adopt a candidate, or delete the entry's
private integration state. Reopen the options flow and use **Recover unknown
group creation** only if you can positively identify the exact group. Enter
its exact `@g.us` ID; let the integration verify that group read-only; then
review the second confirmation showing the group ID and connected WhatsApp
account. This recovery step does not create or modify a group. If you cannot
confirm the exact identity, stop and contact the maintainer.

For exact recovery, WAHA documents listing groups through
`GET /api/{session}/groups` in its [group API reference](https://waha.devlike.pro/docs/how-to/groups/).
The response can include participant information; protect it as private admin
data and never share the raw response. The integration does not search groups
by their mutable display name.

## Harmless communication and lifecycle checks

1. In **Developer tools → Actions**, send a harmless group notification:

   ```yaml
   action: notify.send_message
   target:
     entity_id: notify.waha_guests
   data:
     title: "HA guest-group test"
     message: "Harmless test message; no action is requested."
   ```

   Confirm it reaches only the test group. Check the actual entity ID in the
   entity picker if Home Assistant assigned a suffix.
2. From the ordinary test member account, check that WhatsApp does not let it
   add another participant. This should be denied; do not use an automation
   to try. Then have a group admin create an invite link manually, and use a
   separate test account to request to join through that link. Confirm the
   request remains pending and emits no active membership event until a
   WhatsApp admin manually approves it. After approval, wait for a
   `group.participant.joined` event after roster confirmation. Confirm that
   the payload contains opaque `participant_id`, `membership_id`, and
   per-stay private handle, but not a phone number or raw WhatsApp JID. The
   initial setup roster should have produced a `group.membership.reconciled`
   baseline rather than invented historic join events.
3. From the test member, send a short harmless group message. Confirm one
   `message.received` event with `conversation_type: group`, the exact opaque
   group conversation route, and that member as `sender.participant_id`. Use
   `send_to_conversation` with the event's `conversation_id` to reply in the
   group only if an explicit group response is desired.
4. Send a harmless private DM from the test member to the WAHA account. Confirm
   a direct `message.received` event with a different
   `conversation_id` (the membership-scoped private handle). Use
   `send_to_conversation` with that ID and verify the reply arrives privately.
   The event must not reveal the member's phone or raw JID. The member's
   message can still contain sensitive text; do not test with one.
5. Call `waha_whatsapp.get_group_membership` for the connection. The query
   performs a fresh read-only roster/security reconciliation and may publish
   a membership transition event if it discovers a missed change. It does not
   write to the WhatsApp group. Confirm `ready` and `confirmed` are true,
   `observed_at` is recent, and the active membership matches the event's exact
   `membership_id`. Then query that membership ID directly and confirm
   `membership_found: true`.
6. As a WhatsApp group admin, remove the test member manually. Confirm a
   `group.participant.left` event, the same `participant_id` and old
   `membership_id`, `status: left`, and no usable private route. Try sending
   to the old route manually and confirm it is rejected; do not set up an
   automation to retry it.
7. Re-invite the same test account manually. After the join is confirmed,
   verify the participant ID is retained where identity continuity is
   proven, but the new membership ID and private route differ. Confirm the
   old route remains invalid.

## Restart, offline change, and opt-out checks

1. Restart Home Assistant with the test group intact. Confirm the integration
   performs a fresh roster/security/account check before marking the group
   ready, retains IDs for an unchanged observed stay, and does not create a
   second group. A restored private snapshot by itself is not a live proof.
2. Optional offline-removal check: with a second WhatsApp admin available,
   stop Home Assistant, remove the test member from WhatsApp, then start Home
   Assistant and confirm the fresh roster closes the old membership and
   rejects its route. A leave and rejoin completed entirely during downtime
   cannot be reconstructed reliably from a later snapshot; this test does not
   prove that edge case safe.
3. Turn the feature off in the options flow and reload. Confirm guest routing
   and events stop while individual notify entities still work. Confirm the
   WhatsApp group and its members were not deleted. Re-enable only if desired;
   confirm the saved group is reused, not recreated.
4. Confirm unrelated group chats and unknown senders produce no guest event.
   Confirm the raw direct `send_message` action is not used as a fallback for
   a failed member route.

## Troubleshooting and evidence

Check the `managed_guest_group` diagnostics summary for `ready`, `confirmed`,
`provisioning_status`, `failure_reason`, `revision`, `observed_at`, and active
or closed counts. Common reasons include:

| Reason | What to check |
| --- | --- |
| `unsupported_waha_capabilities` | GOWS engine and version 2026.8.2 or newer. |
| `unverified_bot_account` / `bot_account_changed` | Session is `WORKING`; confirm the linked bot is the same account bound to this saved group. Do not re-pair to another account. |
| `admin_mapping_changed` | Reopen options and review each active HA admin's unique entry-local Person/recipient mapping or explicit exclusion. |
| `admin_add_unconfirmed` / `admin_promotion_unconfirmed` / `admin_role_drift` | Review WhatsApp group admin membership and API response; do not assume a write succeeded. |
| `unsafe_group_settings` / `bot_admin_unconfirmed` | Restore the required WhatsApp settings/role manually, then wait for re-verification. |
| `unexpected_group_admin` | Remove or demote the unreviewed admin in WhatsApp, or review their HA mapping if they are an intended administrator; then wait for re-verification. |
| `guest_webhook_unavailable` | Confirm the private WAHA webhook is healthy and the external server accepts the required events. |
| `group_create_outcome_unknown` | Stop; do not retry creation. Use reviewed recovery if available or ask the maintainer. |
| `guest_registry_unavailable` | Do not toggle/reinstall or create a replacement group. Restore matching integration data from a trusted backup or contact the maintainer. |

When reporting a defect, include the integration version, WAHA engine/version,
test step, approximate UTC time, readiness and redacted failure reason. Do not
send phone numbers, API keys, webhook secrets, raw WAHA payloads, or private
message content. This checklist intentionally does not configure any
automated live group writes.
