"""OpenAI-compatible facade routes for the Bifrost Codex Gateway."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from shared.models import CodexGatewayResponsesRequest

from src.core.database import DbSession
from src.repositories.codex_gateway import CodexGatewayRepository
from src.services.codex_gateway.runtime import (
    CODEX_GATEWAY_KEY_HEADER,
    CodexGatewayRuntime,
    extract_gateway_key,
)


router = APIRouter(tags=["Codex Gateway"])


def get_codex_gateway_runtime(db: DbSession) -> CodexGatewayRuntime:
    return CodexGatewayRuntime(repository=CodexGatewayRepository(db))


@router.post("/v1/responses")
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
    result = await runtime.create_response(
        gateway_key=gateway_key or "",
        payload=payload.model_dump(),
        source_ip=request.client.host if request.client else None,
        client_user_agent=request.headers.get("user-agent"),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)
