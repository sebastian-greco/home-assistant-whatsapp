# Changelog

## 0.2.0

- Publish the app's internal WAHA host, session, and API credentials through
  Home Assistant Supervisor discovery.
- Enable the WAHA WhatsApp HACS integration to connect without exposing port
  3000 or duplicating credentials manually.

## 0.1.1

- Replace the obsolete Supervisor watchdog field with a native Docker health
  check.
- Remove configuration values that duplicate Home Assistant defaults.

## 0.1.0

- Initial experimental HAOS app.
- Add the WAHA 2026.7.1 GOWS engine.
- Add persistent single-session setup.
- Add an ingress-native sidebar control panel with QR and lifecycle controls.
- Keep the WAHA API port disabled by default.
