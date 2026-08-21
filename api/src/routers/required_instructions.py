"""Authenticated resolution and admin settings for required instructions."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.required_instructions import (
    RequiredInstructionsResponse,
    RequiredInstructionsSettings,
)
from src.services.memory import MemoryService
from src.services.required_instructions import RequiredInstructionsService
from src.services.audit import emit_audit
from src.services.authorization import CurrentAuthorizationContext

router = APIRouter(prefix="/api/required-instructions", tags=["Required Instructions"])
admin_router = APIRouter(
    prefix="/api/admin/required-instructions",
    tags=["Required Instructions"],
)


def _service(
    db: DbSession, organization_id: UUID | None
) -> RequiredInstructionsService:
    return RequiredInstructionsService(db, organization_id=organization_id)


async def _require_organization(
    service: RequiredInstructionsService,
    organization_id: UUID,
) -> None:
    if not await service.organization_exists(organization_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )


def _require_required_instructions_boundary(
    authorization: CurrentAuthorizationContext,
    capability: str,
    organization_id: UUID | None,
) -> None:
    authorization.require(capability)
    authorization.require_resource_boundary(organization_id)


@router.get("", response_model=RequiredInstructionsResponse)
async def get_required_instructions(
    db: DbSession,
    current_user: CurrentActiveUser,
) -> RequiredInstructionsResponse:
    memory_settings = await MemoryService(
        db,
        user_id=current_user.user_id,
        organization_id=current_user.organization_id,
    ).settings()
    instructions = await _service(db, current_user.organization_id).resolved(
        memory_enabled=memory_settings["effective_enabled"]
    )
    return RequiredInstructionsResponse(instructions=instructions)


@admin_router.get("", response_model=RequiredInstructionsSettings)
async def get_global_required_instructions(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> RequiredInstructionsSettings:
    _require_required_instructions_boundary(authorization, "configs.read", None)
    instructions = await _service(
        db, authorization.effective_actor.organization_id
    ).configured(None)
    return RequiredInstructionsSettings(instructions=instructions)


@admin_router.put("", response_model=RequiredInstructionsSettings)
async def update_global_required_instructions(
    request: RequiredInstructionsSettings,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> RequiredInstructionsSettings:
    _require_required_instructions_boundary(authorization, "configs.readwrite", None)
    instructions = await _service(
        db, authorization.effective_actor.organization_id
    ).set_configured(
        request.instructions,
        organization_id=None,
        updated_by=authorization.effective_actor.email,
    )
    await emit_audit(
        db,
        "required_instructions.global.update",
        resource_type="required_instructions",
        details={"scope": "platform"},
    )
    await db.commit()
    return RequiredInstructionsSettings(instructions=instructions)


@admin_router.get(
    "/organizations/{organization_id}",
    response_model=RequiredInstructionsSettings,
)
async def get_organization_required_instructions(
    organization_id: UUID,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> RequiredInstructionsSettings:
    _require_required_instructions_boundary(
        authorization, "configs.read", organization_id
    )
    service = _service(db, authorization.effective_actor.organization_id)
    await _require_organization(service, organization_id)
    instructions = await service.configured(organization_id)
    return RequiredInstructionsSettings(instructions=instructions)


@admin_router.put(
    "/organizations/{organization_id}",
    response_model=RequiredInstructionsSettings,
)
async def update_organization_required_instructions(
    organization_id: UUID,
    request: RequiredInstructionsSettings,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
) -> RequiredInstructionsSettings:
    _require_required_instructions_boundary(
        authorization, "configs.readwrite", organization_id
    )
    service = _service(db, authorization.effective_actor.organization_id)
    await _require_organization(service, organization_id)
    instructions = await service.set_configured(
        request.instructions,
        organization_id=organization_id,
        updated_by=authorization.effective_actor.email,
    )
    await emit_audit(
        db,
        "required_instructions.organization.update",
        resource_type="required_instructions",
        resource_id=organization_id,
        details={"organization_id": str(organization_id)},
    )
    await db.commit()
    return RequiredInstructionsSettings(instructions=instructions)
