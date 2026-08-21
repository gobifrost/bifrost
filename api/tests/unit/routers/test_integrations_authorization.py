from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException


_MODULE_PATH = Path(__file__).resolve().parents[3] / "src" / "routers" / "integrations.py"
_SPEC = spec_from_file_location("integrations_router_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
integrations = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = integrations
_SPEC.loader.exec_module(integrations)


class _Auth:
    def __init__(self, kind, organization_id=None, email: str = "admin@example.com") -> None:
        self.selected_boundary = SimpleNamespace(
            kind=kind,
            organization_id=organization_id,
        )
        self.requester = SimpleNamespace(email=email, user_id=uuid4())
        self.calls: list[tuple[str, object | None]] = []

    def require_operation(self, operation_id: str) -> None:
        self.calls.append(("operation", operation_id))

    def require_resource_boundary(self, organization_id) -> None:
        self.calls.append(("resource", organization_id))

    def has_capability(self, capability: str) -> bool:
        return False


class _ConfigRepo:
    def __init__(self, db) -> None:
        self.db = db
        self.calls: list[tuple[str, object]] = []
        self.saved: dict[str, object] | None = None

    async def get_integration_by_id(self, integration_id):
        self.calls.append(("get_integration_by_id", integration_id))
        return SimpleNamespace(id=integration_id)

    async def _save_config(self, **kwargs):
        self.calls.append(("save_config", kwargs))
        self.saved = kwargs

    async def get_integration_defaults(self, integration_id, external=False):
        self.calls.append(("get_defaults", integration_id, external))
        return {"timeout": 30}


class _BatchRepo(_ConfigRepo):
    async def get_mapping_by_org(self, integration_id, organization_id):
        self.calls.append(("get_mapping_by_org", integration_id, organization_id))
        return None

    async def update_mapping(self, *args, **kwargs):
        raise AssertionError("batch route should not reach update_mapping")

    async def create_mapping(self, *args, **kwargs):
        raise AssertionError("batch route should not reach create_mapping")


@pytest.mark.asyncio
async def test_platform_definition_helper_requires_operation_and_platform_boundary() -> None:
    auth = _Auth(integrations.AuthorizationBoundaryKind.PLATFORM)

    integrations._require_platform_integration_definition(
        auth,
        "integrations.config.update",
    )

    assert auth.calls == [
        ("operation", "integrations.config.update"),
        ("resource", None),
    ]


@pytest.mark.asyncio
async def test_mapping_boundary_helper_rejects_managed_boundaries() -> None:
    auth = _Auth(integrations.AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS)

    with pytest.raises(HTTPException) as exc:
        integrations._require_integration_mapping_boundary(auth, uuid4())

    assert exc.value.status_code == 409
    assert auth.calls == []


@pytest.mark.asyncio
async def test_update_integration_config_uses_requester_email(
    monkeypatch,
) -> None:
    repo = _ConfigRepo(db=object())
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda _db: repo)

    auth = _Auth(
        integrations.AuthorizationBoundaryKind.PLATFORM,
        email="ops@example.com",
    )
    ctx = SimpleNamespace(db=object())
    integration_id = uuid4()

    result = await integrations.update_integration_config(
        integration_id=integration_id,
        request=integrations.IntegrationConfigUpdate(
            config={"base_url": "https://example.com"}
        ),
        ctx=ctx,
        authorization=auth,
    )

    assert result.integration_id == integration_id
    assert result.config == {"timeout": 30}
    assert repo.saved is not None
    assert repo.saved["integration_id"] == integration_id
    assert repo.saved["organization_id"] is None
    assert repo.saved["config"] == {"base_url": "https://example.com"}
    assert repo.saved["updated_by"] == "ops@example.com"
    assert auth.calls == [
        ("operation", "integrations.config.update"),
        ("resource", None),
    ]


@pytest.mark.asyncio
async def test_batch_upsert_rejects_managed_boundary(monkeypatch) -> None:
    repo = _BatchRepo(db=object())
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda _db: repo)

    auth = _Auth(integrations.AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS)
    ctx = SimpleNamespace(db=SimpleNamespace(commit=lambda: None))
    integration_id = uuid4()

    request = integrations.IntegrationMappingBatchRequest(
        mappings=[
            {
                "organization_id": uuid4(),
                "entity_id": "tenant-1",
                "entity_name": "Tenant 1",
            }
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await integrations.batch_upsert_mappings(
            integration_id=integration_id,
            request=request,
            ctx=ctx,
            authorization=auth,
        )

    assert exc.value.status_code == 409
    assert repo.calls == [("get_integration_by_id", integration_id)]
    assert auth.calls == [("operation", "integrations.mappings.batch")]
