"""Focused options-flow tests when the Home Assistant runtime is available."""

import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from ._loader import PACKAGE_NAME, load_integration_module  # noqa: E402

const = load_integration_module("const")
sys.modules[PACKAGE_NAME].WahaConfigEntry = object
flow_module = load_integration_module("config_flow")
admins = load_integration_module("guest_admins")


def make_flow(monkeypatch, preview, options=None):
    """Attach only the entry lookup used by OptionsFlow.config_entry."""
    entry = SimpleNamespace(
        entry_id="entry-id",
        options=options or {},
        data={const.CONF_WEBHOOK_SECRET: "private-test-secret"},
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_known_entry=lambda entry_id: entry)
    )
    flow = flow_module.WahaWhatsAppOptionsFlow()
    flow.hass = hass
    flow.handler = "entry-id"

    async def get_preview(_hass, _entry):
        return preview

    monkeypatch.setattr(flow_module, "async_guest_admin_preview", get_preview)

    class FakeRegistry:
        def __init__(self, _hass, _entry_id):
            pass

        async def async_initialize_empty(self):
            entry.initialized = True

    monkeypatch.setattr(flow_module, "GuestRegistry", FakeRegistry)
    return flow, entry


def preview(*, mapped=True, gap=True):
    """Build the small, immutable eligibility snapshot returned by discovery."""
    contacts = (
        (admins.GuestAdminContact("u1", "Host", "person.host", "39333", "39333@c.us"),)
        if mapped
        else ()
    )
    gaps = (admins.GuestAdminGap("u2", "Other", "no_contact"),) if gap else ()
    return admins.GuestAdminPreview(contacts, gaps)


@pytest.mark.asyncio
async def test_guest_group_is_off_by_default(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview())

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["data_schema"]({})[const.CONF_GUEST_GROUP_ENABLED] is False


@pytest.mark.asyncio
async def test_each_unmapped_admin_requires_explicit_exclusion(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview())
    data = {
        const.CONF_GUEST_GROUP_ENABLED: True,
        const.CONF_GUEST_GROUP_NAME: "Guests",
    }

    result = await flow.async_step_init(data)

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unresolved_admins"


@pytest.mark.asyncio
async def test_unknown_admin_cannot_be_excluded(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview())

    result = await flow.async_step_init(
        {
            const.CONF_GUEST_GROUP_ENABLED: True,
            const.CONF_GUEST_GROUP_NAME: "Guests",
            const.CONF_GUEST_GROUP_EXCLUDED_ADMIN_USER_IDS: ["someone-else"],
        }
    )

    assert result["errors"]["base"] == "invalid_exclusions"


@pytest.mark.asyncio
async def test_no_mapped_host_blocks_enable(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview(mapped=False))

    result = await flow.async_step_init(
        {
            const.CONF_GUEST_GROUP_ENABLED: True,
            const.CONF_GUEST_GROUP_NAME: "Guests",
            const.CONF_GUEST_GROUP_EXCLUDED_ADMIN_USER_IDS: ["u2"],
        }
    )

    assert result["errors"]["base"] == "no_mapped_admin"


@pytest.mark.asyncio
async def test_group_name_is_bounded(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview(gap=False))

    result = await flow.async_step_init(
        {
            const.CONF_GUEST_GROUP_ENABLED: True,
            const.CONF_GUEST_GROUP_NAME: "G" * 101,
        }
    )

    assert result["errors"][const.CONF_GUEST_GROUP_NAME] == "invalid_group_name"


@pytest.mark.asyncio
async def test_enable_requires_second_confirmation(monkeypatch) -> None:
    flow, entry = make_flow(monkeypatch, preview())

    first = await flow.async_step_init(
        {
            const.CONF_GUEST_GROUP_ENABLED: True,
            const.CONF_GUEST_GROUP_NAME: "Guests",
            const.CONF_GUEST_GROUP_EXCLUDED_ADMIN_USER_IDS: ["u2"],
        }
    )
    final = await flow.async_step_confirm({})

    assert first["type"] == "form"
    assert first["step_id"] == "confirm"
    assert final["type"] == "create_entry"
    assert final["data"][const.CONF_GUEST_GROUP_ENABLED] is True
    assert final["data"][const.CONF_GUEST_GROUP_EVER_ENABLED] is True
    assert entry.initialized is True
    assert final["data"][const.CONF_GUEST_GROUP_EXCLUDED_ADMIN_USER_IDS] == ["u2"]
    assert final["data"][const.CONF_GUEST_GROUP_REVIEWED_ADMIN_FINGERPRINT] == (
        admins.reviewed_admin_fingerprint(preview(), ["u2"], "private-test-secret")
    )


@pytest.mark.asyncio
async def test_disable_does_not_require_provisioning_confirmation(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview(), {"unrelated": "keep"})

    result = await flow.async_step_init(
        {
            const.CONF_GUEST_GROUP_ENABLED: False,
            const.CONF_GUEST_GROUP_NAME: "Guests",
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"]["unrelated"] == "keep"
    assert result["data"][const.CONF_GUEST_GROUP_EVER_ENABLED] is False


@pytest.mark.asyncio
async def test_prior_enable_marker_survives_disable_and_skips_initialization(
    monkeypatch,
) -> None:
    flow, entry = make_flow(
        monkeypatch,
        preview(gap=False),
        {const.CONF_GUEST_GROUP_EVER_ENABLED: True},
    )
    disabled = await flow.async_step_init({const.CONF_GUEST_GROUP_ENABLED: False})
    assert disabled["data"][const.CONF_GUEST_GROUP_EVER_ENABLED] is True
    entry.options = disabled["data"]
    await flow.async_step_init(
        {const.CONF_GUEST_GROUP_ENABLED: True, const.CONF_GUEST_GROUP_NAME: "Guests"}
    )
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    assert result["data"][const.CONF_GUEST_GROUP_EVER_ENABLED] is True
    assert not hasattr(entry, "initialized")


@pytest.mark.asyncio
async def test_first_enable_store_failure_blocks_opt_in(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview(gap=False))

    async def fail(_self):
        raise OSError("storage unavailable")

    monkeypatch.setattr(flow_module.GuestRegistry, "async_initialize_empty", fail)
    await flow.async_step_init(
        {const.CONF_GUEST_GROUP_ENABLED: True, const.CONF_GUEST_GROUP_NAME: "Guests"}
    )
    result = await flow.async_step_confirm({})
    assert result["type"] == "form"
    assert result["errors"]["base"] == "guest_registry_unavailable"


@pytest.mark.asyncio
async def test_changed_host_mapping_invalidates_confirmation(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview(gap=False))
    first = await flow.async_step_init(
        {const.CONF_GUEST_GROUP_ENABLED: True, const.CONF_GUEST_GROUP_NAME: "Guests"}
    )

    async def changed_preview(_hass, _entry):
        return admins.GuestAdminPreview(
            (
                admins.GuestAdminContact(
                    "u1", "Host", "person.host", "39444", "39444@c.us"
                ),
            ),
            (),
        )

    monkeypatch.setattr(flow_module, "async_guest_admin_preview", changed_preview)
    final = await flow.async_step_confirm({})

    assert first["step_id"] == "confirm"
    assert final["step_id"] == "init"
    assert final["errors"]["base"] == "admin_mapping_changed"


@pytest.mark.asyncio
async def test_changed_person_mapping_invalidates_confirmation(monkeypatch) -> None:
    flow, _entry = make_flow(monkeypatch, preview(gap=False))
    await flow.async_step_init(
        {const.CONF_GUEST_GROUP_ENABLED: True, const.CONF_GUEST_GROUP_NAME: "Guests"}
    )

    async def changed_preview(_hass, _entry):
        return admins.GuestAdminPreview(
            (
                admins.GuestAdminContact(
                    "u1", "Host", "person.replaced", "39333", "39333@c.us"
                ),
            ),
            (),
        )

    monkeypatch.setattr(flow_module, "async_guest_admin_preview", changed_preview)
    result = await flow.async_step_confirm({})

    assert result["errors"]["base"] == "admin_mapping_changed"


@pytest.mark.asyncio
async def test_unknown_create_recovery_requires_exact_id_and_second_confirmation(
    monkeypatch,
) -> None:
    current_preview = preview(gap=False)
    fingerprint = admins.reviewed_admin_fingerprint(
        current_preview, [], "private-test-secret"
    )
    flow, entry = make_flow(
        monkeypatch,
        current_preview,
        {
            const.CONF_GUEST_GROUP_ENABLED: True,
            const.CONF_GUEST_GROUP_NAME: "Guests",
            const.CONF_GUEST_GROUP_REVIEWED_ADMIN_FINGERPRINT: fingerprint,
        },
    )
    manager = object.__new__(flow_module.GuestGroupManager)
    manager.registry = SimpleNamespace(provisioning_status="create_unknown")
    calls = []

    async def review(group_id):
        calls.append(("review", group_id))
        return {"group_id": group_id, "bot_account": "393331111111@c.us"}

    async def adopt(group_id, *, reviewed_bot_account):
        calls.append(("adopt", group_id, reviewed_bot_account))

    manager.async_review_unknown_group = review
    manager.async_adopt_unknown_group = adopt
    entry.runtime_data = SimpleNamespace(guest_manager=manager)
    first = await flow.async_step_init(
        {const.CONF_GUEST_GROUP_ENABLED: True, const.CONF_GUEST_GROUP_NAME: "Guests"}
    )
    assert first["step_id"] == "recover"
    group_id = "120363123456789-1234567890@g.us"
    second = await flow.async_step_recover({"recovery_group_id": group_id})
    assert second["step_id"] == "recover_confirm"
    assert second["description_placeholders"] == {
        "group_id": group_id,
        "bot_account": "393331111111@c.us",
    }
    assert "recovery_group_id" not in entry.options
    final = await flow.async_step_recover_confirm({})
    assert final["type"] == "create_entry"
    assert "recovery_group_id" not in final["data"]
    assert calls == [
        ("review", group_id),
        ("adopt", group_id, "393331111111@c.us"),
    ]
