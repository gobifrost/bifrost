"""Admin API for reusable AI provider connections and model profiles."""

from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from src.core.auth import CurrentActiveUser
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
    AIModelProfileVerifyResponse,
    AIModelsResponse,
    AIProviderConnectionCreate,
    AIProviderConnectionResponse,
    AIProviderConnectionSummary,
    AIProviderConnectionUpdate,
    ModelCatalogResponse,
)
from src.models.contracts.ai_behavior import AIBehaviorResponse, AIBehaviorUpdate
from src.models.contracts.artifacts import ModelCapabilities
from src.models.contracts.llm import LLMModelInfo
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from src.services.ai_model_service import AIModelService, ProviderConnectionTestConfig
from src.services.ai_behavior_service import AIBehaviorService, UNSET
from src.services.model_catalog import catalog_summary, get_model_catalog
from src.services.authorization.enforce import authorize_operation
from src.services.authorization.evaluator import GLOBAL

AI_BEHAVIOR_GET_OPERATION = "GET /api/admin/ai/behavior"
AI_BEHAVIOR_UPDATE_OPERATION = "PUT /api/admin/ai/behavior"
AI_CONNECTIONS_LIST_OPERATION = "GET /api/admin/ai/connections"
AI_CONNECTIONS_CREATE_OPERATION = "POST /api/admin/ai/connections"
AI_CONNECTIONS_VERIFY_OPERATION = "POST /api/admin/ai/connections/verify"
AI_CONNECTION_UPDATE_OPERATION = "PATCH /api/admin/ai/connections/{connection_id}"
AI_CONNECTION_DELETE_OPERATION = "DELETE /api/admin/ai/connections/{connection_id}"
AI_CONNECTION_TEST_OPERATION = "POST /api/admin/ai/connections/{connection_id}/test"
AI_CONNECTION_MODELS_OPERATION = "GET /api/admin/ai/connections/{connection_id}/models"
AI_CATALOG_GET_OPERATION = "GET /api/admin/ai/catalog"
AI_CATALOG_REFRESH_OPERATION = "POST /api/admin/ai/catalog/refresh"
AI_PROFILES_LIST_OPERATION = "GET /api/admin/ai/profiles"
AI_PROFILES_CREATE_OPERATION = "POST /api/admin/ai/profiles"
AI_PROFILES_MERGE_OPERATION = "POST /api/admin/ai/profiles/merge"
AI_PROFILE_UPDATE_OPERATION = "PATCH /api/admin/ai/profiles/{profile_id}"
AI_PROFILE_VERIFY_OPERATION = "POST /api/admin/ai/profiles/{profile_id}/verify"
AI_PROFILE_DELETE_OPERATION = "DELETE /api/admin/ai/profiles/{profile_id}"
AI_ASSIGNMENTS_LIST_OPERATION = "GET /api/admin/ai/assignments"
AI_ASSIGNMENT_SET_OPERATION = "PUT /api/admin/ai/assignments/{assignment_key}"
AI_ASSIGNMENT_DELETE_OPERATION = "DELETE /api/admin/ai/assignments/{assignment_key}"


async def _authorize_platform_setting(
    db: DbSession,
    user: CurrentActiveUser,
    operation: str,
) -> None:
    await authorize_operation(db, user, operation, GLOBAL)


router = APIRouter(
    prefix="/api/admin/ai",
    tags=["AI Model Settings"],
)


@router.get("/behavior")
async def get_ai_behavior(db: DbSession, user: CurrentActiveUser) -> AIBehaviorResponse:
    await _authorize_platform_setting(db, user, AI_BEHAVIOR_GET_OPERATION)
    del user
    behavior = AIBehaviorService(db)
    return AIBehaviorResponse(
        default_system_prompt=await behavior.get_default_system_prompt(),
        agent_execution_mode=await behavior.get_agent_execution_mode(),
    )


@router.put("/behavior")
async def update_ai_behavior(
    request: AIBehaviorUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIBehaviorResponse:
    await _authorize_platform_setting(db, user, AI_BEHAVIOR_UPDATE_OPERATION)
    updated = await AIBehaviorService(db).update_behavior(
        default_system_prompt=(
            request.default_system_prompt
            if "default_system_prompt" in request.model_fields_set
            else UNSET
        ),
        agent_execution_mode=(
            request.agent_execution_mode
            if "agent_execution_mode" in request.model_fields_set
            else UNSET
        ),
        updated_by=user.email,
    )
    await db.commit()
    return AIBehaviorResponse(**updated)


def _raise_service_error(error: Exception) -> NoReturn:
    if isinstance(error, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


def _connection_response(connection: AIProviderConnection) -> AIProviderConnectionResponse:
    assert connection.profile_count is not None, "Connection management queries must load profile counts"
    return AIProviderConnectionResponse(
        id=connection.id,
        name=connection.name,
        provider=connection.provider,
        endpoint=connection.endpoint,
        catalog_provider_id=connection.catalog_provider_id,
        api_key_set=bool(connection.encrypted_api_key),
        profile_count=connection.profile_count,
        anthropic_prompt_cache_supported=connection.anthropic_prompt_cache_supported,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _connection_summary(connection: AIProviderConnection) -> AIProviderConnectionSummary:
    return AIProviderConnectionSummary(
        id=connection.id,
        name=connection.name,
        provider=connection.provider,
        endpoint=connection.endpoint,
        catalog_provider_id=connection.catalog_provider_id,
        anthropic_prompt_cache_supported=connection.anthropic_prompt_cache_supported,
    )


def _profile_response(profile: AIModelProfile) -> AIModelProfileResponse:
    assert profile.referenced_agent_count is not None, "Profile management queries must load agent counts"
    capabilities = ModelCapabilities.model_validate(profile.capabilities) if profile.capabilities else None
    return AIModelProfileResponse(
        id=profile.id,
        name=profile.name,
        connection_id=profile.connection_id,
        model=profile.model,
        capabilities=capabilities,
        enabled_for_chat=profile.enabled_for_chat,
        default_max_tokens=profile.default_max_tokens,
        reasoning_effort=profile.reasoning_effort,
        failover_profile_id=profile.failover_profile_id,
        failover_profile_name=(
            profile.failover_profile.name if profile.failover_profile else None
        ),
        connection=_connection_summary(profile.connection),
        assignment_keys=[assignment.assignment_key for assignment in profile.assignments],
        referenced_agent_count=profile.referenced_agent_count,
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
    await _authorize_platform_setting(db, user, AI_CONNECTIONS_LIST_OPERATION)
    del user
    return [_connection_response(connection) for connection in await AIModelService(db).list_connections()]


@router.post("/connections", status_code=status.HTTP_201_CREATED)
async def create_provider_connection(
    request: AIProviderConnectionCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIProviderConnectionResponse:
    await _authorize_platform_setting(db, user, AI_CONNECTIONS_CREATE_OPERATION)
    del user
    service = AIModelService(db)
    try:
        connection = await service.create_connection(
            name=request.name,
            provider=request.provider,
            api_key=request.api_key,
            endpoint=request.endpoint,
            catalog_provider_id=request.catalog_provider_id,
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
    await _authorize_platform_setting(db, user, AI_CONNECTIONS_VERIFY_OPERATION)
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
    await _authorize_platform_setting(db, user, AI_CONNECTION_UPDATE_OPERATION)
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
            catalog_provider_id=request.catalog_provider_id,
            catalog_provider_id_provided="catalog_provider_id" in request.model_fields_set,
        )
        await db.commit()
        return _connection_response(await service.get_connection(connection.id))
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_connection(connection_id: UUID, db: DbSession, user: CurrentActiveUser) -> None:
    await _authorize_platform_setting(db, user, AI_CONNECTION_DELETE_OPERATION)
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
    await _authorize_platform_setting(db, user, AI_CONNECTION_TEST_OPERATION)
    del user
    try:
        result = await AIModelService(db).test_connection(connection_id)
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
    await _authorize_platform_setting(db, user, AI_CONNECTION_MODELS_OPERATION)
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
        source="catalog" if connection.catalog_provider_id else "provider",
        models=models,
    )


@router.get("/catalog")
async def get_catalog(db: DbSession, user: CurrentActiveUser) -> ModelCatalogResponse:
    """Providers Bifrost can connect to, from the cached models.dev catalog."""
    await _authorize_platform_setting(db, user, AI_CATALOG_GET_OPERATION)
    del user
    return catalog_summary(await get_model_catalog(db))


@router.post(
    "/catalog/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PlatformJobAccepted,
)
async def refresh_catalog(
    response: Response, db: DbSession, user: CurrentActiveUser
) -> PlatformJobAccepted:
    """Refresh the model catalog now instead of waiting for the schedule."""
    await _authorize_platform_setting(db, user, AI_CATALOG_REFRESH_OPERATION)
    from src.jobs.platform.system_maintenance import enqueue_manual_model_catalog_refresh

    job, reused = await enqueue_manual_model_catalog_refresh(db, user)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,
        reused=reused,
    )


@router.get("/profiles")
async def list_model_profiles(db: DbSession, user: CurrentActiveUser) -> list[AIModelProfileResponse]:
    await _authorize_platform_setting(db, user, AI_PROFILES_LIST_OPERATION)
    del user
    return [_profile_response(profile) for profile in await AIModelService(db).list_profiles()]


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_model_profile(
    request: AIModelProfileCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIModelProfileResponse:
    await _authorize_platform_setting(db, user, AI_PROFILES_CREATE_OPERATION)
    del user
    service = AIModelService(db)
    try:
        profile = await service.create_profile(
            name=request.name,
            connection_id=request.connection_id,
            model=request.model,
            capabilities=request.capabilities,
            enabled_for_chat=request.enabled_for_chat,
            default_max_tokens=request.default_max_tokens,
            failover_profile_id=request.failover_profile_id,
            reasoning_effort=request.reasoning_effort,
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
    await _authorize_platform_setting(db, user, AI_PROFILES_MERGE_OPERATION)
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
    await _authorize_platform_setting(db, user, AI_PROFILE_UPDATE_OPERATION)
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
            default_max_tokens=request.default_max_tokens,
            default_max_tokens_provided="default_max_tokens" in request.model_fields_set,
            failover_profile_id=request.failover_profile_id,
            failover_profile_id_provided="failover_profile_id" in request.model_fields_set,
            reasoning_effort=request.reasoning_effort,
            reasoning_effort_provided="reasoning_effort" in request.model_fields_set,
        )
        await db.commit()
        return _profile_response(await service.get_profile(profile.id))
    except (LookupError, ValueError) as error:
        await db.rollback()
        _raise_service_error(error)


@router.post("/profiles/{profile_id}/verify")
async def verify_model_profile(
    profile_id: UUID, db: DbSession, user: CurrentActiveUser
) -> AIModelProfileVerifyResponse:
    await _authorize_platform_setting(db, user, AI_PROFILE_VERIFY_OPERATION)
    del user
    try:
        result = await AIModelService(db).verify_profile(profile_id)
    except LookupError as error:
        _raise_service_error(error)
    return AIModelProfileVerifyResponse(success=result.success, message=result.message)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_profile(profile_id: UUID, db: DbSession, user: CurrentActiveUser) -> None:
    await _authorize_platform_setting(db, user, AI_PROFILE_DELETE_OPERATION)
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
    await _authorize_platform_setting(db, user, AI_ASSIGNMENTS_LIST_OPERATION)
    del user
    return [_assignment_response(assignment) for assignment in await AIModelService(db).list_assignments()]


@router.put("/assignments/{assignment_key}")
async def set_model_assignment(
    assignment_key: AIModelAssignmentKey,
    request: AIModelAssignmentUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AIModelAssignmentResponse:
    await _authorize_platform_setting(db, user, AI_ASSIGNMENT_SET_OPERATION)
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
    await _authorize_platform_setting(db, user, AI_ASSIGNMENT_DELETE_OPERATION)
    del user
    try:
        deleted = await AIModelService(db).clear_assignment(assignment_key)
    except ValueError as error:
        await db.rollback()
        _raise_service_error(error)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model assignment not found")
    await db.commit()
