"""SCIM 2.0 (RFC 7643 / 7644) translation for the provisioning core.

``scim2-models`` validates resources, applies PatchOp requests and parses
filters. Real identity providers are looser than the RFC, so requests are
normalized first:

- Entra sends ``manager`` as a bare id string and omits its ``$ref``.
- Okta (and Entra without its 2020 compatibility flag) removes group members
  with ``{"op": "remove", "path": "members", "value": [{"value": id}]}``.
- Vendor extensions Bifrost has no schema for are kept verbatim ("keep all
  the data") but are not validated or exposed through ``/Schemas``.

Responses are built from the stored attributes, so whatever the identity
provider sent round-trips.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from scim2_models import (
    Context,
    EnterpriseUser,
    Group,
    PatchOp,
    ResourceType,
    SCIMException,
    ScimFilter,
    User,
)
from scim2_models.path.expressions import Comparison, CompareOperator

from src.models.orm.identity_providers import IdentityProviderGroup, IdentityProviderUser
from src.services.identity_provisioning.core import (
    GroupRecord,
    ProvisioningError,
    ProvisioningErrorKind,
    UserRecord,
)

ScimUser = User[EnterpriseUser]

SCIM_MEDIA_TYPE = "application/scim+json"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
KNOWN_USER_SCHEMAS = frozenset({USER_SCHEMA, ENTERPRISE_SCHEMA})
MAX_RESULTS = 200

# Attributes Bifrost assigns or never stores.
_SERVER_OWNED = ("id", "meta", "schemas")
_NEVER_STORED = ("password",)

USER_FILTER_ATTRIBUTES = ("userName", "externalId", "id")
GROUP_FILTER_ATTRIBUTES = ("displayName", "externalId", "id")


class ScimError(Exception):
    """An RFC 7644 §3.12 error response."""

    def __init__(
        self,
        status: int,
        detail: str,
        scim_type: str | None = None,
        *,
        retry_after: int | None = None,
    ):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        # Seconds, sent as Retry-After.
        self.retry_after = retry_after

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schemas": [ERROR_SCHEMA],
            "status": str(self.status),
            "detail": self.detail,
        }
        if self.scim_type:
            body["scimType"] = self.scim_type
        return body


def from_provisioning_error(exc: ProvisioningError) -> ScimError:
    if exc.kind is ProvisioningErrorKind.CONFLICT:
        return ScimError(409, exc.detail, "uniqueness")
    if exc.kind is ProvisioningErrorKind.NOT_FOUND:
        return ScimError(404, exc.detail)
    return ScimError(400, exc.detail, "invalidValue")


def _from_validation_error(exc: ValidationError) -> ScimError:
    first = exc.errors()[0]
    if first["type"].startswith("scim_"):
        ctx = first.get("ctx", {})
        return ScimError(int(ctx.get("status", 400)), first["msg"], ctx.get("scim_type"))
    location = ".".join(str(part) for part in first["loc"])
    detail = f"{first['msg']}: {location}" if location else first["msg"]
    return ScimError(400, detail, "invalidValue")


def _from_scim_exception(exc: SCIMException) -> ScimError:
    return ScimError(exc.status, exc.detail, exc.scim_type or None)


# ---------------------------------------------------------------------------
# Request normalization
# ---------------------------------------------------------------------------


def _split_unknown_extensions(
    body: dict[str, Any], known: frozenset[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate extension objects Bifrost has no schema for."""
    kept: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, value in body.items():
        if key.startswith("urn:") and key not in known:
            unknown[key] = _extension_value(key, value)
        else:
            kept[key] = value
    if "schemas" in kept and isinstance(kept["schemas"], list):
        kept["schemas"] = [s for s in kept["schemas"] if s in known]
    return kept, unknown


def _normalize_enterprise(body: dict[str, Any]) -> None:
    enterprise = body.get(ENTERPRISE_SCHEMA)
    if isinstance(enterprise, dict) and isinstance(enterprise.get("manager"), str):
        enterprise["manager"] = {"value": enterprise["manager"]}


def _require_object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ScimError(400, "The request body must be a JSON object", "invalidSyntax")
    return body


def parse_user(body: Any) -> tuple[Any, dict[str, Any]]:
    """Validate a POST/PUT User body; returns the resource and the unknown
    extensions."""
    raw = copy.deepcopy(_require_object(body))
    for key in (*_SERVER_OWNED[:2], *_NEVER_STORED):
        raw.pop(key, None)
    kept, unknown = _split_unknown_extensions(raw, KNOWN_USER_SCHEMAS)
    kept.setdefault("schemas", [USER_SCHEMA])
    _normalize_enterprise(kept)
    try:
        resource = ScimUser.model_validate(kept)
    except ValidationError as exc:
        raise _from_validation_error(exc) from None
    if not resource.user_name:
        raise ScimError(400, "userName is required", "invalidValue")
    return resource, unknown


def parse_group(body: Any) -> Group:
    raw = copy.deepcopy(_require_object(body))
    for key in _SERVER_OWNED[:2]:
        raw.pop(key, None)
    raw.setdefault("schemas", [GROUP_SCHEMA])
    try:
        resource = Group.model_validate(raw)
    except ValidationError as exc:
        raise _from_validation_error(exc) from None
    if not resource.display_name:
        raise ScimError(400, "displayName is required", "invalidValue")
    return resource


def _patch_operations(body: Any) -> list[dict[str, Any]]:
    body = _require_object(body)
    operations = body.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise ScimError(400, "A PatchOp request needs at least one operation", "invalidSyntax")
    return [_require_object(op) for op in operations]


def _patch_op_message(operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schemas": [PATCH_SCHEMA], "Operations": operations}


_PATCH_VERBS = frozenset({"add", "replace", "remove"})


def _extension_object(unknown: dict[str, Any], schema: str) -> dict[str, Any]:
    target = unknown.setdefault(schema, {})
    if not isinstance(target, dict):
        raise ScimError(400, f"The extension '{schema}' is not an object", "invalidValue")
    return target


def _extension_value(schema: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScimError(400, f"A value for the extension '{schema}' must be an object", "invalidValue")
    return value


def _apply_unknown_extension_ops(
    operations: list[dict[str, Any]], unknown: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply operations aimed at unknown extensions directly to their stored
    objects; return the operations left for the validated model."""
    remaining: list[dict[str, Any]] = []
    for op in operations:
        verb = str(op.get("op", "")).lower()
        # Checked before any operation is applied: the model validates only
        # the operations left for it.
        if verb not in _PATCH_VERBS:
            raise ScimError(400, f"Unsupported patch operation '{op.get('op')}'", "invalidSyntax")
        path = op.get("path")
        value = op.get("value")
        if isinstance(path, str) and path.startswith("urn:") and not any(
            path == schema or path.startswith(f"{schema}:") for schema in KNOWN_USER_SCHEMAS
        ):
            schema, _, attribute = path.rpartition(":")
            if path in unknown or not schema:
                schema, attribute = path, ""
            target = _extension_object(unknown, schema)
            if verb == "remove":
                if attribute:
                    target.pop(attribute, None)
                else:
                    unknown.pop(schema, None)
            elif attribute:
                target[attribute] = value
            else:
                target.update(_extension_value(schema, value))
            continue
        if not path and isinstance(value, dict):
            known_value = {}
            for key, item in value.items():
                if key.startswith("urn:") and key not in KNOWN_USER_SCHEMAS:
                    _extension_object(unknown, key).update(_extension_value(key, item))
                else:
                    known_value[key] = item
            if not known_value:
                continue
            op = {**op, "value": known_value}
        remaining.append(op)
    return remaining


def _normalize_user_ops(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    manager_path = f"{ENTERPRISE_SCHEMA}:manager"
    for op in operations:
        if op.get("path") == manager_path and isinstance(op.get("value"), str):
            op = {**op, "value": {"value": op["value"]}}
        value = op.get("value")
        if not op.get("path") and isinstance(value, dict) and isinstance(
            value.get(ENTERPRISE_SCHEMA), dict
        ):
            value = copy.deepcopy(value)
            _normalize_enterprise(value)
            op = {**op, "value": value}
        normalized.append(op)
    return normalized


def _normalize_group_ops(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for op in operations:
        if (
            str(op.get("op", "")).lower() == "remove"
            and op.get("path") == "members"
            and isinstance(op.get("value"), list)
        ):
            for member in op["value"]:
                member_id = member.get("value") if isinstance(member, dict) else None
                if not isinstance(member_id, str):
                    raise ScimError(400, "Each removed member needs a value", "invalidValue")
                normalized.append(
                    {"op": "remove", "path": f'members[value eq {ScimFilter.quote(member_id)}]'}
                )
            continue
        normalized.append(op)
    return normalized


def apply_user_patch(link: IdentityProviderUser, body: Any) -> tuple[Any, dict[str, Any]]:
    current = stored_user_resource(link)
    known, unknown = _split_unknown_extensions(current, KNOWN_USER_SCHEMAS)
    _normalize_enterprise(known)
    operations = _apply_unknown_extension_ops(_patch_operations(body), unknown)
    operations = _normalize_user_ops(operations)
    try:
        resource = ScimUser.model_validate(known)
        if operations:
            patch = PatchOp[ScimUser].model_validate(
                _patch_op_message(operations), scim_ctx=Context.RESOURCE_PATCH_REQUEST
            )
            patch.patch(resource)
    except ValidationError as exc:
        raise _from_validation_error(exc) from None
    except SCIMException as exc:
        raise _from_scim_exception(exc) from None
    return resource, unknown


def apply_group_patch(
    group: IdentityProviderGroup, member_ids: set[UUID], body: Any
) -> Group:
    current = {
        **stored_group_attributes(group),
        "schemas": [GROUP_SCHEMA],
        "id": str(group.id),
        "members": [{"value": str(member)} for member in sorted(member_ids, key=str)],
    }
    operations = _normalize_group_ops(_patch_operations(body))
    try:
        resource = Group.model_validate(current)
        patch = PatchOp[Group].model_validate(
            _patch_op_message(operations), scim_ctx=Context.RESOURCE_PATCH_REQUEST
        )
        patch.patch(resource)
    except ValidationError as exc:
        raise _from_validation_error(exc) from None
    except SCIMException as exc:
        raise _from_scim_exception(exc) from None
    if not resource.display_name:
        raise ScimError(400, "displayName is required", "invalidValue")
    return resource


# ---------------------------------------------------------------------------
# Resource -> core records
# ---------------------------------------------------------------------------


def _attributes(resource: Any, unknown: dict[str, Any] | None = None) -> dict[str, Any]:
    data = resource.model_dump(mode="json", exclude_none=True, by_alias=True)
    for key in (*_SERVER_OWNED, *_NEVER_STORED):
        data.pop(key, None)
    if unknown:
        data.update(copy.deepcopy(unknown))
    return data


def _primary_email(resource: Any) -> str:
    emails = [e for e in (resource.emails or []) if e.value]
    chosen = (
        next((e for e in emails if e.primary), None)
        or next((e for e in emails if (e.type or "").lower() == "work"), None)
        or (emails[0] if emails else None)
    )
    if chosen is not None:
        return str(chosen.value)
    if resource.user_name and "@" in resource.user_name:
        return resource.user_name
    raise ScimError(400, "The user needs an email address or an email userName", "invalidValue")


def _display_name(resource: Any) -> str | None:
    if resource.display_name:
        return resource.display_name
    name = resource.name
    if name is None:
        return None
    if name.formatted:
        return name.formatted
    parts = [part for part in (name.given_name, name.family_name) if part]
    return " ".join(parts) or None


def user_record(resource: Any, unknown: dict[str, Any]) -> UserRecord:
    if not resource.user_name:
        raise ScimError(400, "userName is required", "invalidValue")
    return UserRecord(
        user_name=resource.user_name,
        email=_primary_email(resource),
        external_id=resource.external_id,
        display_name=_display_name(resource),
        active=None if resource.active is None else bool(resource.active),
        attributes=_attributes(resource, unknown),
    )


def group_record(resource: Group) -> GroupRecord:
    attributes = _attributes(resource)
    attributes.pop("members", None)
    return GroupRecord(
        display_name=str(resource.display_name),
        external_id=resource.external_id,
        attributes=attributes,
    )


def group_member_ids(resource: Group) -> set[UUID]:
    ids: set[UUID] = set()
    for member in resource.members or []:
        try:
            ids.add(UUID(str(member.value)))
        except ValueError:
            raise ScimError(
                400, f"Group member '{member.value}' is not a known user id", "invalidValue"
            ) from None
    return ids


# ---------------------------------------------------------------------------
# Core rows -> responses
# ---------------------------------------------------------------------------


def _meta(resource_type: str, location: str, created: datetime, modified: datetime) -> dict:
    return {
        "resourceType": resource_type,
        "created": created.isoformat(),
        "lastModified": modified.isoformat(),
        "location": location,
    }


def stored_user_resource(link: IdentityProviderUser) -> dict[str, Any]:
    data = copy.deepcopy(link.attributes or {})
    schemas = [USER_SCHEMA]
    schemas.extend(key for key in data if key.startswith("urn:"))
    return {
        **data,
        "schemas": schemas,
        "id": str(link.id),
        "userName": link.user_name,
        **({"externalId": link.external_id} if link.external_id else {}),
        "active": link.active,
    }


def stored_group_attributes(group: IdentityProviderGroup) -> dict[str, Any]:
    data = copy.deepcopy(group.attributes or {})
    data["displayName"] = group.display_name
    if group.external_id:
        data["externalId"] = group.external_id
    return data


def user_response(link: IdentityProviderUser, base_url: str) -> dict[str, Any]:
    location = f"{base_url}/Users/{link.id}"
    return {
        **stored_user_resource(link),
        "meta": _meta("User", location, link.created_at, link.updated_at),
    }


def group_response(
    group: IdentityProviderGroup,
    members: list[tuple[UUID, str]] | None,
    base_url: str,
) -> dict[str, Any]:
    location = f"{base_url}/Groups/{group.id}"
    body: dict[str, Any] = {
        "schemas": [GROUP_SCHEMA],
        "id": str(group.id),
        **stored_group_attributes(group),
        "meta": _meta("Group", location, group.created_at, group.updated_at),
    }
    if members is not None:
        body["members"] = [
            {"value": str(link_id), "display": user_name, "$ref": f"{base_url}/Users/{link_id}"}
            for link_id, user_name in members
        ]
    return body


def project(
    body: dict[str, Any], attributes: str | None, excluded_attributes: str | None
) -> dict[str, Any]:
    """Apply the ``attributes`` / ``excludedAttributes`` query parameters at
    top-level attribute granularity. ``id`` and ``schemas`` always return."""

    def names(value: str | None) -> set[str]:
        return {
            part.strip().split(".")[0].lower()
            for part in (value or "").split(",")
            if part.strip()
        }

    included, excluded = names(attributes), names(excluded_attributes)
    always = {"id", "schemas"}
    if included:
        return {k: v for k, v in body.items() if k in always or k.lower() in included}
    return {k: v for k, v in body.items() if k in always or k.lower() not in excluded}


def list_response(resources: list[dict[str, Any]], total: int, start_index: int) -> dict:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EqualityFilter:
    attribute: str
    value: str


def parse_filter(expression: str | None, allowed: tuple[str, ...]) -> EqualityFilter | None:
    """Accept only ``<attribute> eq "<value>"`` on the attributes identity
    providers look resources up by; anything else is ``invalidFilter``."""
    if not expression:
        return None
    try:
        node = ScimFilter(expression).ast
    except (SCIMException, ValueError) as exc:
        raise ScimError(400, f"Invalid filter: {exc}", "invalidFilter") from None
    if (
        not isinstance(node, Comparison)
        or node.op is not CompareOperator.eq
        or node.attr_path.sub_attr is not None
        or node.attr_path.uri not in (None, "")
        or not isinstance(node.value, str)
    ):
        raise ScimError(
            400,
            f"Only '<attribute> eq \"value\"' filters on {', '.join(allowed)} are supported",
            "invalidFilter",
        )
    attribute = next((a for a in allowed if a.lower() == node.attr_path.attr.lower()), None)
    if attribute is None:
        raise ScimError(
            400, f"Filtering on '{node.attr_path.attr}' is not supported", "invalidFilter"
        )
    return EqualityFilter(attribute, node.value)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def service_provider_config(base_url: str) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://docs.gobifrost.com",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_RESULTS},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer token",
                "description": "A connection token generated in Bifrost",
                "primary": True,
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": f"{base_url}/ServiceProviderConfig",
        },
    }


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(scim_ctx=Context.RESOURCE_QUERY_RESPONSE)


def resource_types(base_url: str) -> list[dict[str, Any]]:
    types = []
    for model, endpoint in ((ScimUser, "/Users"), (Group, "/Groups")):
        resource_type = ResourceType.from_resource(model)
        body = _dump(resource_type)
        body["endpoint"] = endpoint
        body["meta"] = {
            "resourceType": "ResourceType",
            "location": f"{base_url}/ResourceTypes/{body['id']}",
        }
        types.append(body)
    return types


def schemas(base_url: str) -> list[dict[str, Any]]:
    result = []
    for model in (User, EnterpriseUser, Group):
        body = _dump(model.to_schema())
        body["meta"] = {"resourceType": "Schema", "location": f"{base_url}/Schemas/{body['id']}"}
        result.append(body)
    return result
