# WAHA managed-group capability check

Historical research note — 2026-09-27. No groups were created and no live HA
or WhatsApp state was changed. The source tree has since updated its bundled
image to GOWS 2026.9.1 (see `waha/Dockerfile`); this note records the earlier
2026.7.1 gap, not the current image pin. The managed-group implementation and
current documentation are present, but no live linked-session group trial has
been completed. See the [manual test checklist](../GUEST_GROUP_HA_TEST.md).

## Pin

`waha/Dockerfile` pins
`devlikeapro/waha:gows-2026.7.1@sha256:8f3a7b11310594b973a588b2f7de061864c83532ae2cdff43280db0402bead29`.
`waha/config.yaml` is the add-on wrapper config; it does not select an engine
or configure groups/webhooks. WAHA `2026.7.1` is a Core release. The WAHA
release notes say that from `2026.6.1`, former Plus features are included in
Core.

The `2026.7.1` source tree contains `src/api/groups.controller.ts`, but the
tagged controller body could not be fetched for this review. Therefore the
table separates release-delta evidence from exact-tag behavior that remains
unverified. Current, unversioned WAHA docs describe present behavior; they are
not proof of what the pinned image implements.

## Capability matrix

| Capability | Evidence against `2026.7.1` | Conclusion for the pin |
| --- | --- | --- |
| Create group; add/remove/promote/demote participants; list roster | These routes are in current official group docs and are marked GOWS-supported. The exact tagged controller contents and runtime behavior were not retrieved. No post-7.1 introduction was found in the release notes reviewed. | Exact pinned behavior is unverified; confirm on the pinned image before relying on it. |
| Admin-only group-info changes (`info-admin-only`) | Present in current docs/GOWS matrix. The exact 7.1 controller behavior was not retrieved; no post-7.1 introduction was found. | Exact pinned behavior is unverified; verify read-after-write before readiness. |
| Only admins can add members (`member-add-mode`) | WAHA `2026.7.2` release notes explicitly announce this API as new. | Absent from 7.1 by release delta. This is a required safety setting in the managed-group plan. |
| Admin approval for joins (`membership-approval`), join-request API/event | WAHA `2026.8.2` explicitly introduces the setting API, join-request list/approve/reject APIs, and `group.v2.participants.join-request`. | Absent from 7.1 by release delta. This is required by the plan for invite-link joins. |
| Participant lifecycle webhook | Current docs mark GOWS support for `group.v2.participants`; document `payload.type` as `join`, `leave`, `promote`, or `demote`, and changed `participants` with `id`/`role` (`left`, `participant`, `admin`, `superadmin`). They say `group.v2.join`/`group.v2.leave` refer to the bot account joining/being added or leaving/being removed; participant events can duplicate those bot events. The 7.1 event implementation/payload was not inspected. | Use the current schema as a test target, not a verified 7.1 contract. Capture candidate-image webhook samples and test deduplication before release. |
| PN/LID mapping | The Lids API was announced before 7.1, and current docs list `/api/{session}/lids`, `/lids/{lid}`, `/lids/pn/{phoneNumber}`. But `2026.8.1` release notes add GOWS resolution of unknown `@lid` via server query and group `participant.pn` when available. The exact 7.1 GOWS mapping behavior was not inspected. | Do not assume every LID resolves at 7.1; handle absent/ambiguous PN mappings as unresolved and deny private guest routing. Test known and unknown LIDs. |

## Decision and tests still needed

The intended policy cannot be fully configured on the pin: both required
settings are later additions. The **minimum documented WAHA API version is
2026.8.2**. This is a compatibility floor, not a selected replacement image or
digest. Any Dockerfile pin change must wait for actual image, restart/session
storage migration, and integration tests; this research makes no automatic
runtime change and does not choose a newer pin.

Before release, test on the exact candidate image: group create/add/promote and
roster readback; info-admin-only and member-add-mode readback; membership
approval readback and approved/rejected invite-link flows; GOWS webhook bot
self-join/self-leave versus guest join/leave/promote/demote; and PN/LID lookup
for mapped, unknown, and conflicting identities. Do not expose a group as ready
until all required settings and admin roles are verified. Membership conveys
communication eligibility only, never house-access authority.

## Primary sources

- Repo pin: [`waha/Dockerfile`](../../waha/Dockerfile); wrapper settings:
  [`waha/config.yaml`](../../waha/config.yaml).
- Exact WAHA tag/release and tagged source tree:
  [2026.7.1 release](https://github.com/devlikeapro/waha/releases/tag/2026.7.1),
  [2026.7.1 source tree](https://github.com/devlikeapro/waha/tree/2026.7.1),
  [groups controller at tag](https://github.com/devlikeapro/waha/blob/2026.7.1/src/api/groups.controller.ts).
- Release deltas:
  [2026.7.2](https://github.com/devlikeapro/waha/releases/tag/2026.7.2),
  [2026.8.1](https://github.com/devlikeapro/waha/releases/tag/2026.8.1),
  [2026.8.2](https://github.com/devlikeapro/waha/releases/tag/2026.8.2),
  [2026.6.1 Core change](https://github.com/devlikeapro/waha/releases/tag/2026.6.1).
- Current official docs (not version-pinned):
  [Groups](https://waha.devlike.pro/docs/how-to/groups/),
  [Engines support matrix](https://waha.devlike.pro/docs/how-to/engines/),
  [Contacts/LIDs](https://waha.devlike.pro/docs/how-to/contacts/),
  [Core vs Plus](https://waha.devlike.pro/docs/how-to/waha-plus/).
