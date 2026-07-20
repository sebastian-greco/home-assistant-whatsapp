# Changelog

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
