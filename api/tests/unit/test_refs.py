"""Unit tests for :mod:`bifrost.refs`.

Covers UUID pass-through, name lookup, ``path::func`` (workflow), slug (app),
ambiguity, and not-found paths across every supported kind.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from bifrost.refs import (
    AmbiguousRefError,
    RefResolver,
    RefNotFoundError,
    resolve_ref,
)


# ---------------------------------------------------------------------------
# Fake HTTP client
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response``."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://test")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


class FakeClient:
    """Routes-to-payload map wrapped in an async ``get`` method."""

    def __init__(self, routes: dict[str, Any]) -> None:
        # Routes map exact request paths to either payloads or
        # (status_code, payload) tuples.
        self._routes = routes
        self.calls: list[str] = []
        self.request_headers: list[dict[str, str]] = []

    async def get(self, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(path)
        self.request_headers.append(dict(kwargs.get("headers") or {}))
        if path not in self._routes:
            return _FakeResponse(None, status_code=404)
        entry = self._routes[path]
        if isinstance(entry, tuple):
            status_code, payload = entry
            return _FakeResponse(payload, status_code=status_code)
        return _FakeResponse(entry)


# ---------------------------------------------------------------------------
# UUID pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uuid_passthrough_skips_lookup() -> None:
    """A valid UUID short-circuits; no HTTP call is made."""
    client = FakeClient({})
    uid = str(uuid4())

    resolved = await resolve_ref(client, "workflow", uid)

    assert resolved == uid
    assert client.calls == []


@pytest.mark.asyncio
async def test_ref_resolver_forwards_fixed_authorization_boundary() -> None:
    integration_id = str(uuid4())
    client = FakeClient(
        {
            "/api/integrations": {
                "items": [{"id": integration_id, "name": "Pax8"}],
                "total": 1,
            }
        }
    )
    resolver = RefResolver(
        client,
        headers={"X-Bifrost-Boundary": "platform"},
    )

    assert await resolver.resolve("integration", "Pax8") == integration_id
    assert client.request_headers == [
        {"X-Bifrost-Boundary": "platform"}
    ]


# ---------------------------------------------------------------------------
# Per-kind name / UUID / ambiguity / not-found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_org_by_name() -> None:
    org_id = str(uuid4())
    client = FakeClient({"/api/organizations": [{"id": org_id, "name": "Acme"}]})

    assert await resolve_ref(client, "org", "Acme") == org_id


@pytest.mark.asyncio
async def test_resolve_org_ambiguous_surfaces_candidates() -> None:
    id_a, id_b = str(uuid4()), str(uuid4())
    client = FakeClient(
        {
            "/api/organizations": [
                {"id": id_a, "name": "Acme"},
                {"id": id_b, "name": "Acme"},
            ]
        }
    )

    with pytest.raises(AmbiguousRefError) as excinfo:
        await resolve_ref(client, "org", "Acme")

    uuids = {c["uuid"] for c in excinfo.value.candidates}
    assert uuids == {id_a, id_b}
    assert excinfo.value.kind == "org"


@pytest.mark.asyncio
async def test_resolve_org_not_found() -> None:
    client = FakeClient({"/api/organizations": []})

    with pytest.raises(RefNotFoundError):
        await resolve_ref(client, "org", "Missing")


@pytest.mark.asyncio
async def test_resolve_role_by_name_uuid_ambiguous() -> None:
    role_id = str(uuid4())
    dup_a, dup_b = str(uuid4()), str(uuid4())

    client = FakeClient({"/api/roles": [{"id": role_id, "name": "Admin"}]})
    assert await resolve_ref(client, "role", "Admin") == role_id

    # UUID pass-through
    assert await resolve_ref(FakeClient({}), "role", role_id) == role_id

    # Ambiguous
    dup_client = FakeClient(
        {
            "/api/roles": [
                {"id": dup_a, "name": "Admin"},
                {"id": dup_b, "name": "Admin"},
            ]
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(dup_client, "role", "Admin")


@pytest.mark.asyncio
async def test_resolve_workflow_by_path_func() -> None:
    wf_id = str(uuid4())
    client = FakeClient(
        {
            "/api/workflows": [
                {
                    "id": wf_id,
                    "name": "Billing Sync",
                    "function_name": "run_billing_sync",
                    "source_file_path": "/workspace/workflows/billing.py",
                    "relative_file_path": "/workspace/workflows/billing.py",
                    "organization_id": None,
                },
                {
                    "id": str(uuid4()),
                    "name": "Other",
                    "function_name": "other_fn",
                    "source_file_path": "/workspace/workflows/other.py",
                    "relative_file_path": "/workspace/workflows/other.py",
                },
            ]
        }
    )

    resolved = await resolve_ref(
        client, "workflow", "workflows/billing.py::run_billing_sync"
    )
    assert resolved == wf_id


@pytest.mark.asyncio
async def test_resolve_workflow_by_name_fallback() -> None:
    wf_id = str(uuid4())
    client = FakeClient(
        {
            "/api/workflows": [
                {
                    "id": wf_id,
                    "name": "Billing Sync",
                    "function_name": "run_billing_sync",
                    "source_file_path": "/workspace/workflows/billing.py",
                }
            ]
        }
    )

    assert await resolve_ref(client, "workflow", "Billing Sync") == wf_id


@pytest.mark.asyncio
async def test_resolve_workflow_ambiguous_by_name() -> None:
    a, b = str(uuid4()), str(uuid4())
    client = FakeClient(
        {
            "/api/workflows": [
                {"id": a, "name": "Sync", "function_name": "f1"},
                {"id": b, "name": "Sync", "function_name": "f2"},
            ]
        }
    )

    with pytest.raises(AmbiguousRefError) as excinfo:
        await resolve_ref(client, "workflow", "Sync")
    assert len(excinfo.value.candidates) == 2


@pytest.mark.asyncio
async def test_resolve_workflow_not_found() -> None:
    client = FakeClient({"/api/workflows": []})
    with pytest.raises(RefNotFoundError):
        await resolve_ref(client, "workflow", "missing.py::missing_fn")


@pytest.mark.asyncio
async def test_resolve_form_by_name_uuid_and_ambiguous() -> None:
    form_id = str(uuid4())
    client = FakeClient(
        {"/api/forms": [{"id": form_id, "name": "Intake", "organization_id": None}]}
    )
    assert await resolve_ref(client, "form", "Intake") == form_id
    assert await resolve_ref(FakeClient({}), "form", form_id) == form_id

    a, b = str(uuid4()), str(uuid4())
    amb = FakeClient(
        {
            "/api/forms": [
                {"id": a, "name": "Intake", "organization_id": None},
                {"id": b, "name": "Intake", "organization_id": str(uuid4())},
            ]
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(amb, "form", "Intake")


@pytest.mark.asyncio
async def test_resolve_agent_by_name_uuid_and_ambiguous() -> None:
    agent_id = str(uuid4())
    client = FakeClient(
        {"/api/agents": [{"id": agent_id, "name": "Triage", "organization_id": None}]}
    )
    assert await resolve_ref(client, "agent", "Triage") == agent_id
    assert await resolve_ref(FakeClient({}), "agent", agent_id) == agent_id

    a, b = str(uuid4()), str(uuid4())
    amb = FakeClient(
        {
            "/api/agents": [
                {"id": a, "name": "Triage", "organization_id": None},
                {"id": b, "name": "Triage", "organization_id": str(uuid4())},
            ]
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(amb, "agent", "Triage")


@pytest.mark.asyncio
async def test_resolve_app_by_slug_direct_hit() -> None:
    app_id = str(uuid4())
    client = FakeClient(
        {
            "/api/applications/invoices": {
                "id": app_id,
                "slug": "invoices",
                "name": "Invoices",
                "organization_id": None,
            }
        }
    )
    assert await resolve_ref(client, "app", "invoices") == app_id


@pytest.mark.asyncio
async def test_resolve_app_by_name_fallback() -> None:
    app_id = str(uuid4())
    # Slug lookup 404s, then name match against the list endpoint.
    client = FakeClient(
        {
            "/api/applications/Invoices App": (404, {"detail": "not found"}),
            "/api/applications": {
                "applications": [
                    {
                        "id": app_id,
                        "slug": "invoices",
                        "name": "Invoices App",
                        "organization_id": None,
                    }
                ],
                "total": 1,
            },
        }
    )
    assert await resolve_ref(client, "app", "Invoices App") == app_id


@pytest.mark.asyncio
async def test_resolve_app_ambiguous_by_name() -> None:
    a, b = str(uuid4()), str(uuid4())
    client = FakeClient(
        {
            "/api/applications/Invoices": (404, None),
            "/api/applications": {
                "applications": [
                    {"id": a, "slug": "invoices-1", "name": "Invoices", "organization_id": None},
                    {"id": b, "slug": "invoices-2", "name": "Invoices", "organization_id": str(uuid4())},
                ],
                "total": 2,
            },
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(client, "app", "Invoices")


@pytest.mark.asyncio
async def test_resolve_app_not_found() -> None:
    client = FakeClient(
        {
            "/api/applications/nope": (404, None),
            "/api/applications": {"applications": [], "total": 0},
        }
    )
    with pytest.raises(RefNotFoundError):
        await resolve_ref(client, "app", "nope")


@pytest.mark.asyncio
async def test_resolve_integration_by_name_uuid_and_ambiguous() -> None:
    int_id = str(uuid4())
    client = FakeClient(
        {"/api/integrations": {"items": [{"id": int_id, "name": "Pax8"}], "total": 1}}
    )
    assert await resolve_ref(client, "integration", "Pax8") == int_id
    assert await resolve_ref(FakeClient({}), "integration", int_id) == int_id

    a, b = str(uuid4()), str(uuid4())
    amb = FakeClient(
        {
            "/api/integrations": {
                "items": [
                    {"id": a, "name": "Pax8"},
                    {"id": b, "name": "Pax8"},
                ],
                "total": 2,
            }
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(amb, "integration", "Pax8")


@pytest.mark.asyncio
async def test_resolve_table_by_name_uuid_and_ambiguous() -> None:
    table_id = str(uuid4())
    client = FakeClient(
        {
            "/api/tables": {
                "tables": [
                    {"id": table_id, "name": "tickets", "organization_id": None}
                ],
                "total": 1,
            }
        }
    )
    assert await resolve_ref(client, "table", "tickets") == table_id
    assert await resolve_ref(FakeClient({}), "table", table_id) == table_id

    a, b = str(uuid4()), str(uuid4())
    amb = FakeClient(
        {
            "/api/tables": {
                "tables": [
                    {"id": a, "name": "tickets", "organization_id": None},
                    {"id": b, "name": "tickets", "organization_id": str(uuid4())},
                ],
                "total": 2,
            }
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(amb, "table", "tickets")


@pytest.mark.asyncio
async def test_resolve_event_source_by_name_uuid_and_ambiguous() -> None:
    es_id = str(uuid4())
    client = FakeClient(
        {
            "/api/events/sources": {
                "items": [{"id": es_id, "name": "nightly-sync", "organization_id": None}],
                "total": 1,
            }
        }
    )
    assert await resolve_ref(client, "event_source", "nightly-sync") == es_id
    assert await resolve_ref(FakeClient({}), "event_source", es_id) == es_id

    a, b = str(uuid4()), str(uuid4())
    amb = FakeClient(
        {
            "/api/events/sources": {
                "items": [
                    {"id": a, "name": "nightly-sync", "organization_id": None},
                    {"id": b, "name": "nightly-sync", "organization_id": str(uuid4())},
                ],
                "total": 2,
            }
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(amb, "event_source", "nightly-sync")


@pytest.mark.asyncio
async def test_resolve_config_by_key_uuid_and_ambiguous() -> None:
    cfg_id = str(uuid4())
    client = FakeClient(
        {
            "/api/config": [
                {"id": cfg_id, "key": "feature_flag", "value": "on", "org_id": None}
            ]
        }
    )
    assert await resolve_ref(client, "config", "feature_flag") == cfg_id
    assert await resolve_ref(FakeClient({}), "config", cfg_id) == cfg_id

    a, b = str(uuid4()), str(uuid4())
    amb = FakeClient(
        {
            "/api/config": [
                {"id": a, "key": "feature_flag", "value": "on", "org_id": None},
                {
                    "id": b,
                    "key": "feature_flag",
                    "value": "off",
                    "org_id": str(uuid4()),
                },
            ]
        }
    )
    with pytest.raises(AmbiguousRefError):
        await resolve_ref(amb, "config", "feature_flag")


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_reuses_prior_resolution() -> None:
    """Second resolve with the same cache must not re-hit the network."""
    role_id = str(uuid4())
    client = FakeClient({"/api/roles": [{"id": role_id, "name": "Admin"}]})
    cache: dict[tuple[str, str], str] = {}

    first = await resolve_ref(client, "role", "Admin", cache=cache)
    call_count_after_first = len(client.calls)
    second = await resolve_ref(client, "role", "Admin", cache=cache)

    assert first == second == role_id
    assert call_count_after_first == 1
    assert len(client.calls) == 1  # cache hit — no second call


@pytest.mark.asyncio
async def test_unknown_kind_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown ref kind"):
        await resolve_ref(FakeClient({}), "bogus", "x")  # type: ignore[arg-type]
