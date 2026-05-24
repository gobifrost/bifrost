"""OpenAI-compatible facade routes for the Bifrost Codex Gateway."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from shared.models import CodexGatewayResponsesRequest

from src.core.auth import CurrentActiveUser
from src.core.database import DbSession
from src.models.contracts.codex_gateway import (
    CodexGatewayKeyCreateRequest,
    CodexGatewayKeyCreateResponse,
    CodexGatewayKeyListResponse,
    CodexGatewayKeyRecord,
    OpenAICompatibleError,
)
from src.repositories.codex_gateway import (
    CodexGatewayRepository,
    is_plausible_gateway_key,
)
from src.services.audit import emit_audit
from src.services.codex_gateway.runtime import (
    CODEX_GATEWAY_KEY_HEADER,
    CodexGatewayRuntime,
    extract_gateway_key,
)


router = APIRouter(tags=["Codex Gateway"])


def get_codex_gateway_repository(db: DbSession) -> CodexGatewayRepository:
    return CodexGatewayRepository(db)


def get_codex_gateway_runtime(db: DbSession) -> CodexGatewayRuntime:
    return CodexGatewayRuntime(repository=CodexGatewayRepository(db))


def _invalid_gateway_key_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "error": OpenAICompatibleError(
                message="The Bifrost Codex Gateway key is invalid or revoked.",
                code="invalid_gateway_key",
            ).model_dump()
        },
    )


def _key_record_response(record) -> CodexGatewayKeyRecord:
    return CodexGatewayKeyRecord(
        id=record.id,
        user_id=record.user_id,
        project_id=record.project_id,
        name=record.name,
        allowed_models=list(record.allowed_models or []),
        denied_models=list(record.denied_models or []),
        daily_limit=record.daily_limit,
        monthly_limit=record.monthly_limit,
        status=record.status,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
        last_used_at=record.last_used_at,
    )


@router.post(
    "/api/codex-gateway/keys",
    response_model=CodexGatewayKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_codex_gateway_key",
)
async def create_gateway_key(
    payload: CodexGatewayKeyCreateRequest,
    current_user: CurrentActiveUser,
    repository: Annotated[
        CodexGatewayRepository,
        Depends(get_codex_gateway_repository),
    ],
    db: DbSession,
) -> CodexGatewayKeyCreateResponse:
    material = await repository.create_gateway_key(
        user_id=current_user.user_id,
        project_id=payload.project_id,
        name=payload.name,
        allowed_models=payload.allowed_models,
        denied_models=payload.denied_models,
        daily_limit=payload.daily_limit,
        monthly_limit=payload.monthly_limit,
    )
    await emit_audit(
        db,
        "codex_gateway.key.create",
        resource_type="codex_gateway_key",
        resource_id=material.record.id,
        details={
            "project_id": str(material.record.project_id)
            if material.record.project_id
            else None,
            "name": material.record.name,
            "allowed_models": material.record.allowed_models,
            "denied_models": material.record.denied_models,
        },
    )
    return CodexGatewayKeyCreateResponse(
        record=_key_record_response(material.record),
        key=material.plaintext_key,
    )


@router.get(
    "/api/codex-gateway/keys",
    response_model=CodexGatewayKeyListResponse,
    operation_id="list_codex_gateway_keys",
)
async def list_gateway_keys(
    current_user: CurrentActiveUser,
    repository: Annotated[
        CodexGatewayRepository,
        Depends(get_codex_gateway_repository),
    ],
) -> CodexGatewayKeyListResponse:
    keys = await repository.list_gateway_keys_for_user(current_user.user_id)
    return CodexGatewayKeyListResponse(
        items=[_key_record_response(record) for record in keys]
    )


@router.delete(
    "/api/codex-gateway/keys/{key_id}",
    response_model=CodexGatewayKeyRecord,
    operation_id="revoke_codex_gateway_key",
)
async def revoke_gateway_key(
    key_id: UUID,
    current_user: CurrentActiveUser,
    repository: Annotated[
        CodexGatewayRepository,
        Depends(get_codex_gateway_repository),
    ],
    db: DbSession,
) -> CodexGatewayKeyRecord:
    record = await repository.revoke_gateway_key_for_user(
        key_id=key_id,
        user_id=current_user.user_id,
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await emit_audit(
        db,
        "codex_gateway.key.revoke",
        resource_type="codex_gateway_key",
        resource_id=record.id,
        details={
            "project_id": str(record.project_id) if record.project_id else None,
            "name": record.name,
        },
    )
    return _key_record_response(record)


@router.post(
    "/api/v1/responses",
    operation_id="create_codex_gateway_response_api",
)
@router.post("/v1/responses", operation_id="create_codex_gateway_response")
async def create_response(
    request: Request,
    payload: CodexGatewayResponsesRequest,
    runtime: Annotated[CodexGatewayRuntime, Depends(get_codex_gateway_runtime)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_bifrost_codex_key: Annotated[
        str | None,
        Header(alias=CODEX_GATEWAY_KEY_HEADER),
    ] = None,
) -> JSONResponse:
    gateway_key = extract_gateway_key(authorization, x_bifrost_codex_key)
    if not is_plausible_gateway_key(gateway_key):
        return _invalid_gateway_key_response()

    result = await runtime.create_response(
        gateway_key=gateway_key,
        payload=payload.model_dump(),
        source_ip=request.client.host if request.client else None,
        client_user_agent=request.headers.get("user-agent"),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)
