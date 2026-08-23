"""Admin API for reusable AI provider connections and model profiles."""

from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.models.contracts.ai_models import (
    AIConnectionTestResponse,
    AIModelAssignmentKey,
    AIModelAssignmentResponse,
    AIModelAssignmentUpdate,
    AIModelProfileCreate,
    AIModelProfileMergeRequest,
    AIModelProfileMergeResponse,
    AIModelProfileResponse,
    AIModelProfileUpdate,
    AIModelsResponse,
    AIProviderConnectionCreate,
    AIProviderConnectionResponse,
    AIProviderConnectionSummary,
    AIProviderConnectionUpdate,
)
from src.models.contracts.ai_behavior import AIBehaviorResponse, AIBehaviorUpdate
from src.models.contracts.artifacts import ModelCapabilities
from src.models.contracts.llm import LLMModelInfo
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from src.services.ai_model_service import AIModelService, ProviderConnectionTestConfig
from src.services.ai_behavior_service import AIBehaviorService

router = APIRouter(
    prefix="/api/admin/ai",
    tags=["AI Model Settings"],
    dependencies=[RequirePlatformAdmin],
)


@router.get("/behavior")
async def get_ai_behavior(db: DbSession, user: CurrentActiveUser) -> AIBehaviorResponse:
    del user
    return AIBehaviorResponse(
        default_system_prompt=await AIBehaviorService(db).get_default_system_prompt()
    )


@router.put("/behavior")
async def update_ai_behavior(
    request: AIBehaviorUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIBehaviorResponse:
    prompt = await AIBehaviorService(db).set_default_system_prompt(
        request.default_system_prompt,
        updated_by=user.email,
    )
    await db.commit()
    return AIBehaviorResponse(default_system_prompt=prompt)


def _raise_service_error(error: Exception) -> NoReturn:
    if isinstance(error, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


def _connection_response(connection: AIProviderConnection) -> AIProviderConnectionResponse:
    return AIProviderConnectionResponse(
        id=connection.id,
        name=connection.name,
        provider=connection.provider,
        endpoint=connection.endpoint,
        api_key_set=bool(connection.encrypted_api_key),
        profile_count=len(connection.profiles),
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _connection_summary(connection: AIProviderConnection) -> AIProviderConnectionSummary:
    return AIProviderConnectionSummary(
        id=connection.id,
        name=connection.name,
        provider=connection.provider,
        endpoint=connection.endpoint,
    )


def _profile_response(profile: AIModelProfile) -> AIModelProfileResponse:
    capabilities = ModelCapabilities.model_validate(profile.capabilities) if profile.capabilities else None
    return AIModelProfileResponse(
        id=profile.id,
        name=profile.name,
        connection_id=profile.connection_id,
        model=profile.model,
        capabilities=capabilities,
        enabled_for_chat=profile.enabled_for_chat,
        connection=_connection_summary(profile.connection),
        assignment_keys=[assignment.assignment_key for assignment in profile.assignments],
        referenced_agent_count=len(profile.agents),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _assignment_response(assignment: AIModelAssignment) -> AIModelAssignmentResponse:
    return AIModelAssignmentResponse(
        assignment_key=assignment.assignment_key,
        profile_id=assignment.profile_id,
        profile=_profile_response(assignment.profile),
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


@router.get("/connections")
async def list_provider_connections(db: DbSession, user: CurrentActiveUser) -> list[AIProviderConnectionResponse]:
    del user
    return [_connection_response(connection) for connection in await AIModelService(db).list_connections()]


@router.post("/connections", status_code=status.HTTP_201_CREATED)
async def create_provider_connection(
    request: AIProviderConnectionCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIProviderConnectionResponse:
    del user
    service = AIModelService(db)
    try:
        connection = await service.create_connection(
            name=request.name,
            provider=request.provider,
            api_key=request.api_key,
            endpoint=request.endpoint,
        )
        await db.commit()
        return _connection_response(await service.get_connection(connection.id))
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.post("/connections/verify")
async def verify_provider_connection(
    request: AIProviderConnectionCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIConnectionTestResponse:
    del user
    try:
        result = await AIModelService(db).test_connection_config(
            ProviderConnectionTestConfig(
                provider=request.provider,
                api_key=request.api_key,
                endpoint=request.endpoint,
            )
        )
    except ValueError as error:
        _raise_service_error(error)
    return AIConnectionTestResponse(
        success=result.success,
        message=result.message,
        models=[
            LLMModelInfo(
                id=model.id,
                display_name=model.display_name,
                output_modalities=model.output_modalities,
            )
            for model in result.models
        ]
        if result.models
        else None,
    )


@router.patch("/connections/{connection_id}")
async def update_provider_connection(
    connection_id: UUID,
    request: AIProviderConnectionUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIProviderConnectionResponse:
    del user
    service = AIModelService(db)
    try:
        connection = await service.update_connection(
            connection_id,
            name=request.name,
            provider=request.provider,
            api_key=request.api_key,
            endpoint=request.endpoint,
            endpoint_provided="endpoint" in request.model_fields_set,
        )
        await db.commit()
        return _connection_response(await service.get_connection(connection.id))
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_connection(connection_id: UUID, db: DbSession, user: CurrentActiveUser) -> None:
    del user
    service = AIModelService(db)
    try:
        await service.delete_connection(connection_id)
        await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.post("/connections/{connection_id}/test")
async def test_provider_connection(connection_id: UUID, db: DbSession, user: CurrentActiveUser) -> AIConnectionTestResponse:
    del user
    try:
        result = await AIModelService(db).test_saved_connection(connection_id)
    except (LookupError, ValueError) as error:
        _raise_service_error(error)
    return AIConnectionTestResponse(
        success=result.success,
        message=result.message,
        models=[LLMModelInfo(id=model.id, display_name=model.display_name, output_modalities=model.output_modalities) for model in result.models]
        if result.models else None,
    )


@router.get("/connections/{connection_id}/models")
async def list_provider_models(connection_id: UUID, db: DbSession, user: CurrentActiveUser) -> AIModelsResponse:
    del user
    service = AIModelService(db)
    try:
        connection = await service.get_connection(connection_id)
        models = await service.list_models(connection_id)
    except (LookupError, ValueError) as error:
        _raise_service_error(error)
    if models is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not retrieve models from provider")
    return AIModelsResponse(
        provider=connection.provider,
        models=[LLMModelInfo(id=model.id, display_name=model.display_name, output_modalities=model.output_modalities) for model in models],
    )


@router.get("/profiles")
async def list_model_profiles(db: DbSession, user: CurrentActiveUser) -> list[AIModelProfileResponse]:
    del user
    return [_profile_response(profile) for profile in await AIModelService(db).list_profiles()]


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_model_profile(
    request: AIModelProfileCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIModelProfileResponse:
    del user
    service = AIModelService(db)
    try:
        profile = await service.create_profile(
            name=request.name,
            connection_id=request.connection_id,
            model=request.model,
            capabilities=request.capabilities,
            enabled_for_chat=request.enabled_for_chat,
        )
        await db.commit()
        return _profile_response(await service.get_profile(profile.id))
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.post("/profiles/merge")
async def merge_model_profiles(
    request: AIModelProfileMergeRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIModelProfileMergeResponse:
    del user
    service = AIModelService(db)
    try:
        result = await service.merge_profiles(
            profile_ids=request.profile_ids,
            target_profile_id=request.target_profile_id,
        )
        target_profile_id = result.profile.id
        await db.commit()
        return AIModelProfileMergeResponse(
            profile=_profile_response(await service.get_profile(target_profile_id)),
            merged_profile_ids=list(result.merged_profile_ids),
            reassigned_agent_count=result.reassigned_agent_count,
            reassigned_assignment_keys=list(result.reassigned_assignment_keys),
        )
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.patch("/profiles/{profile_id}")
async def update_model_profile(
    profile_id: UUID,
    request: AIModelProfileUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIModelProfileResponse:
    del user
    service = AIModelService(db)
    try:
        profile = await service.update_profile(
            profile_id,
            name=request.name,
            connection_id=request.connection_id,
            model=request.model,
            capabilities=request.capabilities,
            capabilities_provided="capabilities" in request.model_fields_set,
            enabled_for_chat=request.enabled_for_chat,
        )
        await db.commit()
        return _profile_response(await service.get_profile(profile.id))
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_profile(profile_id: UUID, db: DbSession, user: CurrentActiveUser) -> None:
    del user
    service = AIModelService(db)
    try:
        await service.delete_profile(profile_id)
        await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.get("/assignments")
async def list_model_assignments(db: DbSession, user: CurrentActiveUser) -> list[AIModelAssignmentResponse]:
    del user
    return [_assignment_response(assignment) for assignment in await AIModelService(db).list_assignments()]


@router.put("/assignments/{assignment_key}")
async def set_model_assignment(
    assignment_key: AIModelAssignmentKey,
    request: AIModelAssignmentUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIModelAssignmentResponse:
    del user
    service = AIModelService(db)
    try:
        assignment = await service.set_assignment(assignment_key, request.profile_id)
        await db.commit()
        assignments = await service.list_assignments()
        return _assignment_response(
            next(item for item in assignments if item.assignment_key == assignment.assignment_key)
        )
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.delete("/assignments/{assignment_key}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_model_assignment(assignment_key: AIModelAssignmentKey, db: DbSession, user: CurrentActiveUser) -> None:
    del user
    try:
        deleted = await AIModelService(db).clear_assignment(assignment_key)
    except ValueError as error:
        await db.rollback()
        _raise_service_error(error)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model assignment not found")
    await db.commit()
