"""Optional group setup must preserve established WAHA channels."""

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components import waha_whatsapp as integration  # noqa: E402
from custom_components.waha_whatsapp.api import WahaResponseError  # noqa: E402


@pytest.mark.asyncio
async def test_group_webhook_failure_falls_back_to_direct_and_poll_events() -> None:
    calls = []

    async def ensure(_url, _secret, *, group_events=False):
        calls.append(group_events)
        if group_events:
            raise WahaResponseError("group events unavailable")

    client = SimpleNamespace(async_ensure_webhook=ensure)

    result = await integration._async_ensure_channel_webhook(
        client, "http://homeassistant:8123/api/webhook/x", "secret", group_events=True
    )

    assert result is False
    assert calls == [True, False]


@pytest.mark.asyncio
async def test_direct_webhook_failure_still_blocks_setup() -> None:
    async def ensure(_url, _secret, *, group_events=False):
        raise WahaResponseError("baseline webhook unavailable")

    client = SimpleNamespace(async_ensure_webhook=ensure)

    with pytest.raises(WahaResponseError, match="baseline webhook unavailable"):
        await integration._async_ensure_channel_webhook(
            client,
            "http://homeassistant:8123/api/webhook/x",
            "secret",
            group_events=True,
        )


def test_guest_capability_gate_accepts_haos_build_metadata() -> None:
    server = SimpleNamespace(version="2026.9.1+haos", engine="GOWS")
    assert integration._supports_guest_webhooks(server) is True
