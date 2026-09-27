"""Entry-local Home Assistant administrator mapping tests."""

from types import SimpleNamespace

import pytest

from tests._loader import load_integration_module

admins = load_integration_module("guest_admins")


class _Auth:
    def __init__(self, users):
        self.users = users

    async def async_get_users(self):
        return self.users


class _States:
    def __init__(self, states):
        self.states = states

    def async_all(self):
        return self.states


def _user(id, *, admin=True, active=True, system=False, name=None):
    return SimpleNamespace(
        id=id,
        is_admin=admin,
        is_active=active,
        is_system_generated=system,
        name=name,
    )


def _person(entity_id, user_id):
    return SimpleNamespace(entity_id=entity_id, attributes={"user_id": user_id})


def _entry(*contacts):
    subentries = {}
    for idx, (person_id, recipient) in enumerate(contacts):
        subentries[str(idx)] = SimpleNamespace(
            subentry_type="recipient",
            data={"person_entity_id": person_id, "recipient": recipient},
        )
    return SimpleNamespace(subentries=subentries)


@pytest.mark.asyncio
async def test_admin_mapping_uses_only_entry_contacts_and_person_link():
    hass = SimpleNamespace(
        auth=_Auth([_user("u1", name="Sebastian"), _user("u2", name="Wife")]),
        states=_States([_person("person.seba", "u1"), _person("person.wife", "u2")]),
    )
    preview = await admins.async_guest_admin_preview(
        hass, _entry(("person.seba", "+39 333 123 4567"))
    )
    assert len(preview.contacts) == 1
    assert preview.contacts[0].user_id == "u1"
    assert preview.contacts[0].chat_id == "393331234567@c.us"
    assert [(gap.user_id, gap.reason) for gap in preview.gaps] == [("u2", "no_contact")]


@pytest.mark.asyncio
async def test_system_inactive_and_non_admin_users_are_not_hosts():
    hass = SimpleNamespace(
        auth=_Auth(
            [
                _user("system", system=True),
                _user("inactive", active=False),
                _user("regular", admin=False),
            ]
        ),
        states=_States([_person("person.system", "system")]),
    )
    preview = await admins.async_guest_admin_preview(hass, _entry())
    assert preview.contacts == ()
    assert preview.gaps == ()


@pytest.mark.asyncio
async def test_duplicate_admin_phone_is_ambiguous():
    hass = SimpleNamespace(
        auth=_Auth([_user("u1"), _user("u2")]),
        states=_States([_person("person.one", "u1"), _person("person.two", "u2")]),
    )
    preview = await admins.async_guest_admin_preview(
        hass,
        _entry(("person.one", "+393331234567"), ("person.two", "+393331234567")),
    )
    assert preview.contacts == ()
    assert {(gap.user_id, gap.reason) for gap in preview.gaps} == {
        ("u1", "shared_contact"),
        ("u2", "shared_contact"),
    }


def test_review_fingerprint_binds_mappings_gaps_and_exclusions():
    preview = admins.GuestAdminPreview(
        (
            admins.GuestAdminContact(
                "u1", "Sebastian", "person.seba", "+39111", "39111@c.us"
            ),
        ),
        (admins.GuestAdminGap("u2", "Guest", "no_contact"),),
    )
    approved = admins.reviewed_admin_fingerprint(preview, ["u2"], "private-secret")
    assert "+39111" not in approved
    assert approved == admins.reviewed_admin_fingerprint(
        preview, ["u2"], "private-secret"
    )
    assert approved != admins.reviewed_admin_fingerprint(preview, [], "private-secret")
    changed = admins.GuestAdminPreview(
        preview.contacts,
        (admins.GuestAdminGap("u2", "Guest", "no_person"),),
    )
    assert approved != admins.reviewed_admin_fingerprint(
        changed, ["u2"], "private-secret"
    )
