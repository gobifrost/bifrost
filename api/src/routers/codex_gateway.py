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
    CodexGatewayOAuthAccountRecord,
    CodexGatewayOAuthConnectResponse,
    CodexGatewayOAuthDisconnectResponse,
    CodexGatewayOAuthImportRequest,
    CodexGatewayOAuthImportResponse,
    CodexGatewayOAuthStatusResponse,
    OpenAICompatibleError,
)
from src.repositories.codex_gateway import (
    CodexGatewayKeyLimitError,
    CodexGatewayRepository,
    is_plausible_gateway_key,
)
from src.services.audit import emit_audit
from src.services.codex_gateway.oauth import CodexAuthCacheError, parse_codex_auth_cache
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


def _oauth_account_response(record) -> CodexGatewayOAuthAccountRecord:
    return CodexGatewayOAuthAccountRecord(
        id=record.id,
        user_id=record.user_id,
        provider=getattr(record, "provider", "chatgpt_codex"),
        upstream_subject=record.upstream_subject,
        upstream_email=record.upstream_email,
        upstream_workspace_id=record.upstream_workspace_id,
        access_token_expires_at=record.access_token_expires_at,
        scopes=list(getattr(record, "scopes", None) or []),
        last_refresh_at=record.last_refresh_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
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
    try:
        material = await repository.create_gateway_key(
            user_id=current_user.user_id,
            project_id=payload.project_id,
            name=payload.name,
            allowed_models=payload.allowed_models,
            denied_models=payload.denied_models,
            daily_limit=payload.daily_limit,
            monthly_limit=payload.monthly_limit,
        )
    except CodexGatewayKeyLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
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


@router.get(
    "/api/codex-gateway/oauth/status",
    operation_id="get_codex_gateway_oauth_status",
)
async def get_oauth_status(
    current_user: CurrentActiveUser,
    repository: Annotated[
        CodexGatewayRepository,
        Depends(get_codex_gateway_repository),
    ],
) -> CodexGatewayOAuthStatusResponse:
    account = await repository.get_active_upstream_account_for_user(
        current_user.user_id
    )
    return CodexGatewayOAuthStatusResponse(
        connected=account is not None,
        account=_oauth_account_response(account) if account is not None else None,
    )


@router.post(
    "/api/codex-gateway/oauth/connect",
    operation_id="start_codex_gateway_oauth_connect",
)
async def start_oauth_connect(
    _current_user: CurrentActiveUser,
) -> CodexGatewayOAuthConnectResponse:
    return CodexGatewayOAuthConnectResponse()


@router.post(
    "/api/codex-gateway/oauth/import-auth-cache",
    operation_id="import_codex_gateway_oauth_auth_cache",
)
async def import_oauth_auth_cache(
    payload: CodexGatewayOAuthImportRequest,
    current_user: CurrentActiveUser,
    repository: Annotated[
        CodexGatewayRepository,
        Depends(get_codex_gateway_repository),
    ],
    db: DbSession,
) -> CodexGatewayOAuthImportResponse:
    try:
        parsed = parse_codex_auth_cache(payload.auth_cache)
    except CodexAuthCacheError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    account = await repository.upsert_upstream_account_for_user(
        user_id=current_user.user_id,
        upstream_subject=parsed.upstream_subject,
        upstream_email=parsed.upstream_email,
        upstream_workspace_id=parsed.upstream_workspace_id,
        access_token=parsed.access_token,
        refresh_token=parsed.refresh_token,
        access_token_expires_at=parsed.access_token_expires_at,
        scopes=parsed.scopes,
    )
    await emit_audit(
        db,
        "codex_gateway.oauth.import",
        resource_type="codex_gateway_upstream_account",
        resource_id=account.id,
        details={
            "provider": getattr(account, "provider", "chatgpt_codex"),
            "upstream_workspace_id": account.upstream_workspace_id,
            "has_refresh_token": parsed.refresh_token is not None,
        },
    )
    return CodexGatewayOAuthImportResponse(
        connected=True,
        account=_oauth_account_response(account),
    )


@router.delete(
    "/api/codex-gateway/oauth",
    operation_id="disconnect_codex_gateway_oauth",
)
async def disconnect_oauth_account(
    current_user: CurrentActiveUser,
    repository: Annotated[
        CodexGatewayRepository,
        Depends(get_codex_gateway_repository),
    ],
    db: DbSession,
) -> CodexGatewayOAuthDisconnectResponse:
    account = await repository.revoke_upstream_account_for_user(
        user_id=current_user.user_id
    )
    if account is not None:
        await emit_audit(
            db,
            "codex_gateway.oauth.disconnect",
            resource_type="codex_gateway_upstream_account",
            resource_id=account.id,
            details={
                "provider": getattr(account, "provider", "chatgpt_codex"),
                "upstream_workspace_id": account.upstream_workspace_id,
            },
        )
    return CodexGatewayOAuthDisconnectResponse(revoked=account is not None)


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
