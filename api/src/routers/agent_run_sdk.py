"""The two agent-run routes consumed by the SDK and worker socket."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from shared.sdk_agent_runs import (
    SdkAgentRunError,
    enqueue_sdk_agent_run,
    get_sdk_agent_run,
)
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.agent_runs import (
    AgentRunDetailResponse,
    AgentRunEnqueueRequest,
    AgentRunEnqueueResponse,
    PausedResponse,
)
from src.services.run_retention.settings import run_not_found_suffix


detail_router = APIRouter(prefix="/api/agent-runs", tags=["Agent Runs"])
enqueue_router = APIRouter(prefix="/api/agent-runs", tags=["Agent Runs"])


@detail_router.get("/{run_id}")
async def get_agent_run(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentRunDetailResponse:
    """Get agent run detail with steps."""
    try:
        return await get_sdk_agent_run(db, user, run_id=run_id)
    except SdkAgentRunError as e:
        detail = e.detail
        if e.status_code == status.HTTP_404_NOT_FOUND:
            detail = f"Agent run {run_id} not found." + await run_not_found_suffix(db)
        raise HTTPException(status_code=e.status_code, detail=detail) from None


@enqueue_router.post(
    "/enqueue",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AgentRunEnqueueResponse | PausedResponse,
)
async def enqueue_agent_run_request(
    request: AgentRunEnqueueRequest,
    response: Response,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentRunEnqueueResponse | PausedResponse:
    """Queue an agent run and return without waiting for execution."""
    try:
        result = await enqueue_sdk_agent_run(
            db,
            user,
            agent_name=request.agent_name,
            input_data=request.input,
            output_schema=request.output_schema,
            run_as=request.run_as,
        )
    except SdkAgentRunError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    if isinstance(result, PausedResponse):
        response.status_code = status.HTTP_200_OK
    return result
