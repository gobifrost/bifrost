"""E2E: real requests behind the R2c decision matrix's denials.

The matrix compares the new evaluator with a legacy oracle restated from the
access list. This probe grounds the oracle's DENIALS in the running API: for
every REST entry the oracle says a persona cannot reach at home, send the
request as that persona (random-UUID path params, ``{}`` body, or a minimal
valid body where the handler checks permissions after validating it) and
require 401/403. Only the denied side is probed, so nothing here mutates state unless
the oracle is wrong, and a non-403 is a finding about the oracle's input.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest

from src.services.access_list import ACCESS_LIST
from tests.unit.authorization.legacy_oracle import PERSONAS, legacy_decide

pytestmark = pytest.mark.e2e

_PERSONA = {persona.name: persona for persona in PERSONAS}
_METHODS_WITH_BODY = {"POST", "PUT", "PATCH", "DELETE"}

# Entries whose handler answers 422/404 before its inline check can run, so a
# random-UUID request with an empty body cannot show the denial. Each is a
# deny-unless-admin (or -bypass) check the handler makes after validating the
# body or loading the object.
_BODY_FIRST = "body validation (422) runs before the inline check"
_LOOKUP_FIRST = "object lookup (404) runs before the inline check"
INCONCLUSIVE: dict[tuple[str, str], str] = {
    ("POST", "/api/applications"): _BODY_FIRST,
    ("POST", "/api/applications/swap-slugs"): _BODY_FIRST,
    ("PUT", "/api/applications/{app_id}/draft"): _BODY_FIRST,
    ("POST", "/api/applications/{app_id}/logo"): _BODY_FIRST,
    ("POST", "/api/applications/{app_id}/replace"): _BODY_FIRST,
    ("POST", "/api/applications/{app_id}/rollback"): _BODY_FIRST,
    ("POST", "/api/events/emit"): _BODY_FIRST,
    ("POST", "/api/mcp-connections"): _BODY_FIRST,
    ("DELETE", "/api/applications/{app_id}"): _LOOKUP_FIRST,
    ("PATCH", "/api/applications/{app_id}"): _LOOKUP_FIRST,
    ("PUT", "/api/applications/{app_id}/dependencies"): _LOOKUP_FIRST,
    ("DELETE", "/api/applications/{app_id}/files/{file_path}"): _LOOKUP_FIRST,
    ("PUT", "/api/applications/{app_id}/files/{file_path}"): _LOOKUP_FIRST,
    ("DELETE", "/api/applications/{app_id}/logo"): _LOOKUP_FIRST,
    ("POST", "/api/applications/{app_id}/publish"): _LOOKUP_FIRST,
    ("DELETE", "/api/mcp-connections/{connection_id}"): _LOOKUP_FIRST,
    ("PATCH", "/api/mcp-connections/{connection_id}"): _LOOKUP_FIRST,
    ("POST", "/api/mcp-connections/{connection_id}/connect"): _LOOKUP_FIRST,
    ("POST", "/api/mcp-connections/{connection_id}/refresh-tools"): _LOOKUP_FIRST,
    ("PATCH", "/api/mcp-connections/{connection_id}/tools/{tool_id}"): _LOOKUP_FIRST,
}

# Entries whose handler validates the body (422) before it decides the
# permission. A minimal valid body lets the decision run; a wrong oracle
# would let it through, which is the finding this probe exists to report.
_PROBE_ID = str(uuid4())
VALID_BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/api/audit/exports"): {
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2026-01-02T00:00:00Z",
        "action": "access.check",
    },
    ("POST", "/api/identities"): {"name": "deny-probe", "organization_id": None},
    ("PATCH", "/api/identities/{identity_id}"): {"name": "deny-probe"},
    ("POST", "/api/organizations"): {"name": "deny-probe"},
    ("POST", "/api/roles"): {"name": "deny-probe"},
    ("PUT", "/api/roles/{role_id}/permissions"): {"permissions": []},
    ("DELETE", "/api/roles/{role_id}/users"): {"user_ids": [_PROBE_ID]},
    ("POST", "/api/roles/{role_id}/users"): {"user_ids": [_PROBE_ID]},
    ("POST", "/api/users"): {"email": "deny-probe@example.com"},
    ("PATCH", "/api/users/bulk"): {
        "user_ids": [_PROBE_ID],
        "operation": "set_active",
        "is_active": True,
    },
    ("POST", "/api/users/{user_id}/invite/send"): {
        "registration_url": "https://example.com/accept-invite?token=deny-probe"
    },
    ("POST", "/api/users/{user_id}/access/check"): {"organization_id": "global", "operation": "tables.update"},
    ("PUT", "/api/users/{user_id}/role-assignments"): {"base_role_id": _PROBE_ID},
    ("POST", "/auth/admin/revoke-user"): {"user_id": _PROBE_ID},
}


def _probeable(persona_name: str) -> list:
    persona = _PERSONA[persona_name]
    return [
        entry
        for entry in ACCESS_LIST
        if entry.method not in (None, "WS")
        and entry.key not in INCONCLUSIVE
        and not legacy_decide(persona, entry, cross=False)
    ]


def _send(e2e_client, headers: dict, entry):
    path = re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), entry.path)
    kwargs = {"headers": headers}
    if entry.method in _METHODS_WITH_BODY:
        kwargs["json"] = VALID_BODIES.get(entry.key, {})
    return e2e_client.request(entry.method, path, **kwargs)


def _probe(e2e_client, headers: dict, entries: list) -> list[str]:
    failures = []
    for entry in entries:
        response = _send(e2e_client, headers, entry)
        if response.status_code not in (401, 403):
            failures.append(f"{entry.method} {entry.path} -> {response.status_code}")
    return failures


def test_regular_user_is_refused_where_the_oracle_denies(e2e_client, org1_user) -> None:
    entries = _probeable("regular")
    assert entries, "the oracle denies nothing for a regular user?"
    failures = _probe(e2e_client, org1_user.headers, entries)
    assert not failures, f"{len(failures)} of {len(entries)} probes were not refused:\n" + "\n".join(failures)


def test_provider_member_is_refused_where_the_oracle_denies(e2e_client, provider_org_user) -> None:
    entries = _probeable("provider_member")
    assert entries, "the oracle denies nothing for a provider member?"
    failures = _probe(e2e_client, provider_org_user.headers, entries)
    assert not failures, f"{len(failures)} of {len(entries)} probes were not refused:\n" + "\n".join(failures)


def test_inconclusive_and_body_entries_are_in_the_access_list() -> None:
    keys = {entry.key for entry in ACCESS_LIST}
    unknown = [key for key in (*INCONCLUSIVE, *VALID_BODIES) if key not in keys]
    assert not unknown, f"INCONCLUSIVE or VALID_BODIES entries not in the access list: {unknown}"
