# Home Assistant WhatsApp

Self-hosted WhatsApp notifications for Home Assistant, powered by
[WAHA](https://waha.devlike.pro/).

This repository contains the two pieces needed for a native experience:

- **WAHA for Home Assistant**, an HAOS app that runs and preserves the linked
  WhatsApp session and provides a sidebar control panel.
- **WAHA WhatsApp**, a HACS custom integration that creates native Home
  Assistant notify entities and sends through the app's private API.

There are no Meta templates or per-message provider fees. WAHA uses an
unofficial WhatsApp protocol, so use a dedicated, replaceable household number
and understand that WhatsApp can restrict it.

## Current functionality

- Free-form outbound WhatsApp notifications.
- A native `notify` entity for every configured Home Assistant Person.
- Family, Adults, and Guests fan-out notify entities.
- Automatic, private discovery of the HAOS app by the HACS integration.
- Manual connection support for WAHA running elsewhere on the network.
- A direct action for sending to an arbitrary phone number.
- QR linking, session lifecycle controls, diagnostics, and persistent backups.

Inbound messages, replies, buttons, guest access provisioning, and household
commands are intentionally future phases.

## Requirements

- Home Assistant 2026.7 or newer.
- Home Assistant OS on `amd64` for the included app, or an independently
  managed WAHA server.
- HACS for the native Home Assistant integration.
- A dedicated WhatsApp account linked to WAHA as a companion device.

## Install the HAOS app

1. Go to **Settings → Apps → App store → Repositories**.
2. Add:

   ```text
   https://github.com/sebastian-greco/home-assistant-whatsapp
   ```

3. Refresh the app store and install **WAHA for Home Assistant**.
4. Configure a random API key of at least 16 characters; 32 or more is
   recommended.
5. Start the app and enable **Show in sidebar**.
6. Open **WAHA**, show the QR code, and scan it from WhatsApp under
   **Linked devices**.
7. Wait for the session status to become `WORKING`.

Keep port `3000` disabled. The HACS integration communicates with WAHA over
Home Assistant's private app network and receives the connection details
through Supervisor discovery.

## Install the HACS integration

1. Open HACS and select **Custom repositories**.
2. Add the same repository URL with type **Integration**.
3. Download **WAHA WhatsApp** and restart Home Assistant.
4. Restart the **WAHA for Home Assistant** app once so it republishes
   discovery if necessary.
5. Open **Settings → Devices & services** and select the discovered
   **WAHA WhatsApp** card.

If discovery is unavailable, select **Add integration → WAHA WhatsApp** and
enter a reachable WAHA URL, API key, and session manually.

## Add people and phone numbers

Open the configured WAHA WhatsApp integration and select
**Add WhatsApp recipient**. For each recipient:

- Select an existing `person.*` entity.
- Enter their international WhatsApp phone number including country code.
- Choose **Family member** or **Guest**.
- Optionally include them in the **Adults** group.

The Person's current display name becomes the notify entity name. Home
Assistant users are normally already associated with a Person. A visitor can
be created as a Person without creating a login account, which lets the same
recipient model work for guests.

The integration creates entities similar to:

- `notify.sebastian`
- `notify.lucila`
- `notify.family`
- `notify.adults`
- `notify.guests`

Home Assistant may retain an existing entity ID or add a suffix, so confirm
the actual ID in the entity picker.

## Send a notification

Use the modern Home Assistant notify action:

```yaml
actions:
  - action: notify.send_message
    target:
      entity_id: notify.sebastian
    data:
      title: Garage warning
      message: The garage door has been open for 10 minutes.
```

The WhatsApp message is rendered as a bold title followed by the details. To
fan out independently to a household group, target `notify.family`,
`notify.adults`, or `notify.guests`.

Existing notification routers can keep their `title` and `message` contract:

```yaml
sequence:
  - variables:
      whatsapp_targets:
        sebastian: notify.sebastian
        lucila: notify.lucila
        family: notify.family
        adults: notify.adults
        guests: notify.guests
      whatsapp_target: "{{ whatsapp_targets.get(person) }}"

  - action: notify.send_message
    continue_on_error: true
    target:
      entity_id: "{{ whatsapp_target }}"
    data:
      title: "{{ final_title }}"
      message: "{{ message }}"
```

For a one-off number that is not configured as a Person, use the direct
integration action:

```yaml
actions:
  - action: waha_whatsapp.send_message
    data:
      config_entry_id: YOUR_CONFIG_ENTRY_ID
      to: "+39 333 123 4567"
      title: Washing machine
      message: The cycle has finished.
      link_preview: true
```

The automation editor provides a config-entry picker, so the ID does not need
to be typed when building the action in the UI.

## Migrating from the Kapso integration

Version `1.0.0` replaces the old `kapso_whatsapp` domain with
`waha_whatsapp`; it is deliberately a clean provider migration because Kapso
credentials and templates cannot be converted into a WAHA linked session.

Before installing this version:

1. Remove the **Kapso WhatsApp** integration from Devices & services.
2. Remove its old HACS download and restart Home Assistant.
3. Install the WAHA app and the new HACS integration using the instructions
   above.
4. Re-add recipients by selecting their existing Person entities.
5. Update automation targets if Home Assistant did not preserve the same
   desired notify entity IDs.

The complete Kapso source and documentation remain permanently available on
the [`legacy-kapso`](https://github.com/sebastian-greco/home-assistant-whatsapp/tree/legacy-kapso)
branch. Existing `v0.1.0` and `v0.2.0` release tags are unchanged.

## Security

- Never expose WAHA port 3000 to the internet.
- Keep the sidebar restricted to Home Assistant administrators.
- Keep media downloads disabled until media support is needed.
- Enable WhatsApp two-step verification and configure a recovery email.
- Keep the dedicated SIM/eSIM recoverable in a normal phone.
- API keys and phone numbers are redacted from downloaded diagnostics.

## Versions and development

The two deliverables are independently versioned:

- HACS integration: `custom_components/waha_whatsapp/manifest.json`, released
  as `vMAJOR.MINOR.PATCH`.
- HAOS app: `waha/config.yaml`, released as `waha-vMAJOR.MINOR.PATCH` and
  published as `ghcr.io/sebastian-greco/ha-waha:VERSION`.

Local validation:

```bash
uv run ruff check .
uv run python scripts/check_version.py
uv run python -m compileall -q custom_components tests
uv run pytest
node --check waha/run.mjs
node --check waha/control.mjs
node --check waha/discovery.mjs
node --test waha/tests/*.test.mjs
```

See [CHANGELOG.md](CHANGELOG.md), [RELEASING.md](RELEASING.md), and
[`waha/DOCS.md`](waha/DOCS.md) for more details.
