# Changelog

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
