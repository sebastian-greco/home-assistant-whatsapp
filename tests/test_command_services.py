"""Command declarations require an HA admin and entry-local contact allowlist."""

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from homeassistant.exceptions import ServiceValidationError  # noqa: E402

from custom_components import waha_whatsapp as integration  # noqa: E402


class _Registry:
    def __init__(self):
        self.calls = []

    async def async_register(self, *args):
        self.calls.append(args)

    async def async_unregister(self, name):
        self.calls.append(("unregister", name))

    def list_commands(self):
        return [
            {
                "name": "status",
                "aliases": [],
                "contact_ids": [],
                "allow_current_guests": True,
            }
        ]


def _call(*, is_admin=True, active=True, user_id="admin"):
    async def get_user(_user_id):
        return SimpleNamespace(is_admin=is_admin, is_active=active)

    hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=get_user))
    return SimpleNamespace(
        hass=hass,
        context=SimpleNamespace(user_id=user_id),
        data={
            "config_entry_id": "entry-a",
            "name": "status",
            "aliases": ["house"],
            "allowed_contacts": ["notify.seba"],
            "allow_current_guests": False,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admin", "active", "user_id"),
    [
        (False, True, "ordinary"),
        (True, False, "inactive"),
        (True, True, None),
    ],
)
async def test_non_admin_cannot_manage_commands(admin, active, user_id) -> None:
    call = _call(is_admin=admin, active=active, user_id=user_id)
    with pytest.raises(ServiceValidationError, match="administrator"):
        await integration._async_handle_register_command(call)


@pytest.mark.asyncio
async def test_admin_registration_resolves_entry_local_subentry(monkeypatch) -> None:
    registry = _Registry()
    entry = SimpleNamespace(
        entry_id="entry-a", runtime_data=SimpleNamespace(command_registry=registry)
    )
    monkeypatch.setattr(integration, "_loaded_entry", lambda _hass, _id: entry)
    monkeypatch.setattr(
        integration,
        "_configured_recipient",
        lambda _hass, _entity: (entry, "39333", "person.seba"),
    )
    monkeypatch.setattr(
        integration.er,
        "async_get",
        lambda _hass: SimpleNamespace(
            async_get=lambda _entity: SimpleNamespace(config_subentry_id="contact-a")
        ),
    )
    call = _call()

    await integration._async_handle_register_command(call)
    assert registry.calls == [("status", ["house"], ["contact-a"], False)]

    call.data = {"config_entry_id": "entry-a", "name": "status"}
    assert await integration._async_handle_list_commands(call) == {
        "commands": registry.list_commands()
    }
    await integration._async_handle_unregister_command(call)
    assert registry.calls[-1] == ("unregister", "status")


@pytest.mark.asyncio
async def test_admin_cannot_allow_contact_from_another_entry(monkeypatch) -> None:
    registry = _Registry()
    entry = SimpleNamespace(
        entry_id="entry-a", runtime_data=SimpleNamespace(command_registry=registry)
    )
    other = SimpleNamespace(entry_id="entry-b")
    monkeypatch.setattr(integration, "_loaded_entry", lambda _hass, _id: entry)
    monkeypatch.setattr(
        integration,
        "_configured_recipient",
        lambda _hass, _entity: (other, "39333", None),
    )
    with pytest.raises(ServiceValidationError, match="another WAHA entry"):
        await integration._async_handle_register_command(_call())
    assert registry.calls == []
