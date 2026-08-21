"""Authorization boundaries for remaining admin config routes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.memory import MemoryPlatformSettingsUpdate
from src.models.contracts.required_instructions import RequiredInstructionsSettings
from src.routers import llm_config, memory, required_instructions, sandbox_runner_admin
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "ops@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Ops User",
        organization_id=None,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.parametrize(
    ("helper", "capability"),
    [
        (llm_config._require_llm_config, "configs.read"),
        (memory._require_platform_memory, "configs.read"),
        (sandbox_runner_admin._require_sandbox_runner, "platformjobs.read"),
    ],
)
def test_platform_helpers_require_explicit_platform_boundary(
    helper, capability
) -> None:
    authorization = _authorization(
        capabilities={capability},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        helper(authorization, capability)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_embedding_reindex_requires_knowledge_write() -> None:
    authorization = _authorization(capabilities={"configs.readwrite"})

    with pytest.raises(HTTPException) as exc:
        llm_config._require_embedding_reindex(authorization)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: knowledge.readwrite"


def test_required_instructions_requires_exact_org_boundary() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        capabilities={"configs.read"},
        boundary=AuthorizationBoundary.platform(),
    )

    with pytest.raises(HTTPException) as exc:
        required_instructions._require_required_instructions_boundary(
            authorization,
            "configs.read",
            organization_id,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        f"select organization:{organization_id}"
    )


@pytest.mark.asyncio
async def test_update_platform_memory_settings_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_by_values: list[str] = []
    audit_events: list[tuple[str, dict[str, object]]] = []
    commits = 0

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _Service:
        def __init__(self, db, *, user_id, organization_id):  # noqa: ANN001
            self.db = db
            self.user_id = user_id
            self.organization_id = organization_id

        async def set_platform_enabled(self, enabled: bool, *, updated_by: str) -> None:
            assert enabled is True
            updated_by_values.append(updated_by)

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    monkeypatch.setattr(memory, "MemoryService", _Service)
    monkeypatch.setattr(memory, "emit_audit", _emit_audit)

    result = await memory.update_platform_memory_settings(
        MemoryPlatformSettingsUpdate(enabled=True),
        _DB(),
        _authorization(
            capabilities={"configs.readwrite"},
            email="operator@example.com",
        ),
    )

    assert result.enabled is True
    assert updated_by_values == ["operator@example.com"]
    assert commits == 1
    assert audit_events == [
        (
            "memory.platform_settings.update",
            {
                "resource_type": "memory_platform_settings",
                "details": {"enabled": True},
            },
        )
    ]


@pytest.mark.asyncio
async def test_update_global_required_instructions_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_by_values: list[str] = []
    audit_events: list[tuple[str, dict[str, object]]] = []
    commits = 0

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _Service:
        def __init__(self, db, *, organization_id):  # noqa: ANN001
            self.db = db
            self.organization_id = organization_id

        async def set_configured(
            self,
            instructions: str,
            *,
            organization_id,
            updated_by: str,
        ) -> str:
            assert organization_id is None
            updated_by_values.append(updated_by)
            return instructions

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    monkeypatch.setattr(
        required_instructions,
        "RequiredInstructionsService",
        _Service,
    )
    monkeypatch.setattr(required_instructions, "emit_audit", _emit_audit)

    result = await required_instructions.update_global_required_instructions(
        RequiredInstructionsSettings(instructions="Always be concise."),
        _DB(),
        _authorization(
            capabilities={"configs.readwrite"},
            email="operator@example.com",
        ),
    )

    assert result.instructions == "Always be concise."
    assert updated_by_values == ["operator@example.com"]
    assert commits == 1
    assert audit_events == [
        (
            "required_instructions.global.update",
            {
                "resource_type": "required_instructions",
                "details": {"scope": "platform"},
            },
        )
    ]
