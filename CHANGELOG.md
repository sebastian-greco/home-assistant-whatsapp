# Changelog

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.2] - 2026-07-27

### Fixed

- Correlate GOWS poll votes by the engine-stable WhatsApp message token when
  WAHA changes the serialized message-ID chat envelope from `@c.us` on send to
  `@lid` on the vote webhook.
- Resolve webhook LIDs through WAHA's session LID mapping API before comparing
  the voter and poll destination with the configured phone-number chat.
- Canonicalize persisted pending poll IDs during restore so polls created by
  1.2.0 or 1.2.1 remain correlatable after updating.

### Security

- Require WAHA to map every alternate LID to the configured `@c.us` contact.
  Unknown, unmapped, or mismatched LIDs continue to reject the vote without
  firing a Home Assistant event.

## [1.2.1] - 2026-07-27

### Fixed

- Accept GOWS direct-message poll votes when WhatsApp identifies the voter with
  an alternate numeric `@lid`, while continuing to require the authenticated
  webhook, configured session, exact outgoing message ID and direction, and
  exact phone-number destination chat.
- Add sanitized rejection warnings and diagnostic counters so authenticated
  poll votes no longer fail silently when parsing or correlation rejects them.

### Security

- Keep mismatched phone-number JIDs, malformed LIDs, destination changes, and
  group-chat identities rejected. Diagnostics never expose phone numbers,
  message IDs, actions, or poll contents.

## [1.2.0] - 2026-07-27

### Added

- Add single-selection WhatsApp actionable polls through
  `waha_whatsapp.send_poll`, accepting the existing Companion App `action` and
  `title` dictionaries.
- Publish settled WhatsApp choices on the existing
  `mobile_app_notification_action` event so current action-handler automations
  remain compatible.
- Configure a private WAHA poll webhook automatically, with SHA-512 HMAC,
  outbound message/recipient correlation, persisted pending state, and a
  configurable five-second vote-correction window.
- Add a non-triggering fallback choice for notifications with only one real
  action.

### Security

- Preserve unrelated WAHA session webhooks while managing the integration's
  callback, and keep webhook credentials and correlation data private and
  redacted from diagnostics.

### Behavior

- Keep workflow execution, state guards, semantic expiry, and confirmation
  messages in Home Assistant automations. Failed/undecodable votes never fire
  an action or send an automatic response.

## [1.1.0] - 2026-07-24

### Changed

- Make the Home Assistant Person association optional identity metadata and
  allow individual contacts to use an independently configured display name.
- Expose the associated `person_entity_id` as non-sensitive state metadata on
  individual notify entities so routers can discover contacts dynamically.
- Keep notification routing entirely contact-based. A possible broadcast to
  all configured contacts is explicitly separate and remains unimplemented.

### Removed

- Remove Family, Adults, and Guests recipient settings and fan-out notify
  entities.

### Migration

- Migrate config entries to version 1.2 by removing only obsolete group fields
  and the three exact integration-owned group entities while preserving every
  individual contact, Person association, and individual notify entity.

## [1.0.1] - 2026-07-21

### Fixed

- Reload the integration automatically after recipients are added, updated, or
  removed so individual entities and household group membership stay current.
- Use the stable shared device name `WAHA` so new entity IDs are generated as
  `notify.waha_<recipient>` and `notify.waha_<group>` instead of including the
  account name and WAHA version.
- Avoid duplicate config-entry reloads on Home Assistant 2026.6 and newer by
  routing entry changes through one update listener.

### Documentation

- Correct the notify entity examples and add a staged Companion App-to-WhatsApp
  migration guide, including actionable-notification and fallback guidance.

## [1.0.0] - 2026-07-21

### Added

- A WAHA-native HACS integration for free-form outbound WhatsApp messages.
- Automatic Supervisor discovery between the HAOS app and HACS integration.
- Recipient subentries that select existing Home Assistant Person entities and
  associate them with WhatsApp phone numbers.
- Native individual, Family, Adults, and Guests notify entities.
- A direct `waha_whatsapp.send_message` action for arbitrary phone numbers.
- Redacted diagnostics and manual support for externally hosted WAHA servers.

### Changed

- Replaced the Kapso Cloud API and template system with the self-hosted WAHA
  API and linked-device session.
- Renamed the integration domain from `kapso_whatsapp` to `waha_whatsapp`.
- Renamed and refocused the repository as Home Assistant WhatsApp.

### Removed

- Kapso credentials, approved templates, authentication templates, and the
  24-hour free-form messaging restriction.

### Migration

- This is an intentional breaking provider migration. Remove the Kapso
  integration before installing WAHA WhatsApp and re-add recipient mappings.
- The complete Kapso v0.2 state remains on the `legacy-kapso` branch and the
  original `v0.1.0` and `v0.2.0` tags remain unchanged.

[1.0.0]: https://github.com/sebastian-greco/home-assistant-whatsapp/releases/tag/v1.0.0
[1.0.1]: https://github.com/sebastian-greco/home-assistant-whatsapp/releases/tag/v1.0.1
[1.1.0]: https://github.com/sebastian-greco/home-assistant-whatsapp/releases/tag/v1.1.0
[1.2.0]: https://github.com/sebastian-greco/home-assistant-whatsapp/releases/tag/v1.2.0
[1.2.1]: https://github.com/sebastian-greco/home-assistant-whatsapp/releases/tag/v1.2.1
[1.2.2]: https://github.com/sebastian-greco/home-assistant-whatsapp/releases/tag/v1.2.2
