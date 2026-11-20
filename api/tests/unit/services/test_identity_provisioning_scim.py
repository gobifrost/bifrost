"""SCIM request translation, including the identity-provider quirks it
normalizes."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.models.orm.identity_providers import IdentityProviderGroup, IdentityProviderUser
from src.services.identity_provisioning import scim
from src.services.identity_provisioning.scim import ScimError

ENTERPRISE = scim.ENTERPRISE_SCHEMA
CUSTOM = "urn:ietf:params:scim:schemas:extension:CustomExt:2.0:User"


def _patch(*operations: dict) -> dict:
    return {"schemas": [scim.PATCH_SCHEMA], "Operations": list(operations)}


def _link(**attributes) -> IdentityProviderUser:
    now = datetime.now(timezone.utc)
    return IdentityProviderUser(
        id=uuid4(),
        user_id=uuid4(),
        external_id="ext-1",
        user_name="pat@contoso.example",
        active=True,
        attributes={
            "userName": "pat@contoso.example",
            "emails": [{"value": "pat@contoso.example", "type": "work", "primary": True}],
            **attributes,
        },
        created_at=now,
        updated_at=now,
    )


def test_entra_create_keeps_extensions_and_drops_the_password():
    resource, unknown = scim.parse_user(
        {
            "schemas": [scim.USER_SCHEMA, ENTERPRISE, CUSTOM],
            "userName": "Pat@Contoso.example",
            "active": "True",
            "externalId": "ext-1",
            "password": "never-stored",
            "emails": [{"value": "pat@contoso.example", "type": "work", "primary": True}],
            ENTERPRISE: {"department": "Support", "manager": "manager-id"},
            CUSTOM: {"costCenter": "42"},
        }
    )
    record = scim.user_record(resource, unknown)

    assert record.active is True
    assert record.email == "pat@contoso.example"
    assert record.attributes[ENTERPRISE]["manager"] == {"value": "manager-id"}
    assert record.attributes[CUSTOM] == {"costCenter": "42"}
    assert "password" not in record.attributes


def test_discovery_does_not_advertise_the_password_it_drops():
    user = next(s for s in scim.schemas("https://bifrost.example") if s["id"] == scim.USER_SCHEMA)
    names = {attribute["name"] for attribute in user["attributes"]}

    assert "password" not in names
    assert {"userName", "emails", "active"} <= names


def test_user_without_email_or_email_username_is_refused():
    resource, unknown = scim.parse_user({"userName": "pat"})

    with pytest.raises(ScimError) as raised:
        scim.user_record(resource, unknown)

    assert raised.value.status == 400


def test_entra_patch_quirks_apply():
    resource, unknown = scim.apply_user_patch(
        _link(),
        _patch(
            {"op": "Replace", "path": "active", "value": "False"},
            {"op": "Add", "path": f"{ENTERPRISE}:manager", "value": "manager-2"},
            {"op": "replace", "path": 'emails[type eq "work"].value', "value": "pat2@contoso.example"},
            {"op": "Replace", "value": {"displayName": "Pat Two"}},
        ),
    )
    record = scim.user_record(resource, unknown)

    assert record.active is False
    assert record.email == "pat2@contoso.example"
    assert record.display_name == "Pat Two"
    assert record.attributes[ENTERPRISE]["manager"]["value"] == "manager-2"


def test_patch_on_an_unknown_extension_is_kept_verbatim():
    resource, unknown = scim.apply_user_patch(
        _link(**{CUSTOM: {"costCenter": "42"}}),
        _patch({"op": "replace", "path": f"{CUSTOM}:costCenter", "value": "43"}),
    )

    assert scim.user_record(resource, unknown).attributes[CUSTOM] == {"costCenter": "43"}


def test_a_replace_without_active_leaves_it_unsaid():
    resource, unknown = scim.parse_user(
        {"schemas": [scim.USER_SCHEMA], "userName": "pat@contoso.example"}
    )

    assert scim.user_record(resource, unknown).active is None


def test_an_unsupported_verb_on_an_unknown_extension_changes_nothing():
    link = _link(**{CUSTOM: {"costCenter": "42"}})

    with pytest.raises(ScimError) as raised:
        scim.apply_user_patch(
            link, _patch({"op": "merge", "path": f"{CUSTOM}:costCenter", "value": "43"})
        )

    assert raised.value.status == 400
    assert link.attributes[CUSTOM] == {"costCenter": "42"}


def test_flattened_extension_attributes_are_filed_under_their_schema():
    resource, unknown = scim.parse_user(
        {
            "schemas": [scim.USER_SCHEMA, ENTERPRISE],
            "userName": "pat@contoso.example",
            f"{ENTERPRISE}:department": "Support",
            ENTERPRISE: {"employeeNumber": "42"},
            f"{CUSTOM}:costCenter": "7",
        }
    )
    record = scim.user_record(resource, unknown)

    assert record.attributes[ENTERPRISE]["department"] == "Support"
    assert record.attributes[ENTERPRISE]["employeeNumber"] == "42"
    assert record.attributes[CUSTOM] == {"costCenter": "7"}


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": CUSTOM, "value": "not an object"},
        {"op": "add", "value": {CUSTOM: "not an object"}},
    ],
)
def test_a_whole_extension_value_must_be_an_object(operation):
    with pytest.raises(ScimError) as raised:
        scim.apply_user_patch(_link(**{CUSTOM: {"costCenter": "42"}}), _patch(operation))

    assert raised.value.status == 400


def test_a_body_nested_past_any_scim_resource_is_refused():
    from src.routers.scim import _too_deep

    shallow: dict = {"userName": "pat@contoso.example", "name": {"givenName": "Pat"}}
    deep: dict = {}
    level = deep
    for _ in range(64):
        level["x"] = {}
        level = level["x"]

    assert not _too_deep(shallow)
    assert _too_deep(deep)


def test_a_patch_that_removes_the_user_name_is_invalid():
    with pytest.raises(ScimError) as raised:
        resource, unknown = scim.apply_user_patch(
            _link(), _patch({"op": "remove", "path": "userName"})
        )
        scim.user_record(resource, unknown)

    assert raised.value.status == 400


def test_patch_cannot_change_the_id():
    with pytest.raises(ScimError) as raised:
        scim.apply_user_patch(_link(), _patch({"op": "replace", "path": "id", "value": "x"}))

    assert raised.value.scim_type == "mutability"


def test_okta_member_removal_with_a_value_list_is_normalized():
    now = datetime.now(timezone.utc)
    group = IdentityProviderGroup(
        id=uuid4(), display_name="Techs", attributes={}, created_at=now, updated_at=now
    )
    keep, drop, add = uuid4(), uuid4(), uuid4()

    resource = scim.apply_group_patch(
        group,
        {keep, drop},
        _patch(
            {"op": "remove", "path": "members", "value": [{"value": str(drop)}]},
            {"op": "add", "path": "members", "value": [{"value": str(add)}]},
        ),
    )

    assert scim.group_member_ids(resource) == {keep, add}


@pytest.mark.parametrize(
    ("expression", "attribute", "value"),
    [
        ('userName eq "Pat@Contoso.example"', "userName", "Pat@Contoso.example"),
        ('externalId eq "ext-1"', "externalId", "ext-1"),
    ],
)
def test_supported_filters(expression, attribute, value):
    parsed = scim.parse_filter(expression, scim.USER_FILTER_ATTRIBUTES)

    assert parsed == scim.EqualityFilter(attribute, value)


@pytest.mark.parametrize(
    "expression",
    ['userName sw "pat"', 'userName eq "a" and active eq true', 'title eq "x"', "garbage(("],
)
def test_unsupported_filters_are_invalid_filter_errors(expression):
    with pytest.raises(ScimError) as raised:
        scim.parse_filter(expression, scim.USER_FILTER_ATTRIBUTES)

    assert (raised.value.status, raised.value.scim_type) == (400, "invalidFilter")


def test_excluded_attributes_keep_id_and_schemas():
    body = {"schemas": ["s"], "id": "1", "displayName": "G", "members": [{"value": "x"}]}

    assert scim.project(body, None, "members") == {"schemas": ["s"], "id": "1", "displayName": "G"}
