# WAHA for Home Assistant

## Before starting

Create a long random API key (at least 16 characters). A password manager-generated 32-character value is ideal. The app privately publishes this key to the companion Home Assistant integration through Supervisor discovery; it is never displayed by the discovery flow.

## First start

1. Set `api_key` in the Configuration tab and save.
2. Start the app.
3. Enable **Show in sidebar** if Home Assistant did not enable it automatically.
4. Open **WAHA** in the sidebar.
5. When the session reports `SCAN_QR_CODE`, select **Show QR**.
6. On the phone holding the dedicated house WhatsApp account, open **Linked devices**, select **Link a device**, and scan the QR.
7. Wait for the status to become `WORKING`.

## Connect the Home Assistant integration

Install **WAHA WhatsApp** through HACS using the repository URL
`https://github.com/sebastian-greco/home-assistant-whatsapp`, then restart Home
Assistant. Restart this app once if the discovered integration is not already
visible under **Settings → Devices & services**. Confirm the discovered card,
then add recipients by selecting existing Home Assistant Person entities and
entering their WhatsApp phone numbers.

The API port can remain disabled. Home Assistant connects over the private app
network using credentials passed through Supervisor discovery.

The app persists the linked session and restarts it after Home Assistant or app restarts. A cold backup stops the app briefly so the session database is copied consistently.

## Controls and logs

The sidebar control panel covers the normal household workflow: health, version, QR, and session start/stop/restart. Use **Settings → Apps → WAHA** to start or stop the whole container and inspect full logs.

WAHA's native dashboard does not currently understand Home Assistant's tokenized ingress base path. It is therefore not the default sidebar UI. If advanced debugging is temporarily necessary, map container port `3000/tcp` to an unused host port in the Network section, then open `http://HOME_ASSISTANT_IP:PORT/dashboard`. Sign in as `admin` and use the configured API key as the password. Remove the port mapping afterward.

The prebuilt `ghcr.io/sebastian-greco/ha-waha` package is public so fresh HAOS
installations can pull it without registry credentials.

## Security

- Do not forward WAHA port 3000 from your router or expose it through a public reverse proxy.
- Keep the app sidebar restricted to Home Assistant administrators.
- Keep media downloads disabled for the notification-only phase.
- Store the dedicated eSIM in a recoverable phone, enable WhatsApp two-step verification, and add a recovery email.
- WAHA uses an unofficial WhatsApp protocol. A dedicated number reduces impact but does not remove the risk of account restriction.

## Resource use

The app uses the browserless GOWS engine. WAHA's own guidance estimates roughly 200 MB RAM for one GOWS session. Actual use varies during login, synchronization, and message activity.
