# Implementation plans

Plans describe intended work, not installed functionality. Each plan records
scope, public contracts, implementation phases, verification, and the user/API
documentation required before release. Proposed fields and actions must not be
treated as available until the implementation and release documentation say so.

## Next priority

- [Managed guest group](MANAGED_GUEST_GROUP.md): one opt-in WhatsApp guest
  group, persistent participant/membership identities, membership events,
  group notifications, and private communication with current members.
  Status: planned; implementation not started; release version not assigned.
- [WAHA group capabilities](WAHA_GROUP_CAPABILITIES.md): version-specific
  prerequisite research. The current 2026.7.1 engine lacks required membership
  security APIs; a tested HAOS app update is part of the feature plan.

Guest groups take priority over the previously proposed voice-transcription
phase. Voice transcription, command execution, access provisioning, and sidebar
improvements are not part of this plan.

## Earlier design records

- [v1.4 implementation plan](../V1_4_0_PLAN.md)
- [WhatsApp channel architecture](../WHATSAPP_CHANNEL_DESIGN.md)

Keep historical plans in place. Link new work here and update the relevant
user-facing reference when it actually ships.
