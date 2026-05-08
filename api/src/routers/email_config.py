"""
Email Configuration Admin Router

Admin endpoints for managing email workflow configuration.
Requires platform admin access.
"""

import logging

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, Field

from src.core.auth import CurrentActiveUser, CurrentSuperuser, RequirePlatformAdmin
from src.core.database import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.email import (
    EmailTestRequest,
    EmailWorkflowConfigRequest,
    EmailWorkflowConfigResponse,
    EmailWorkflowValidationResponse,
)
from src.services.email_service import EmailService, send_email

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/email",
    tags=["Email Configuration"],
    dependencies=[RequirePlatformAdmin],
)


@router.get("/config")
async def get_email_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> EmailWorkflowConfigResponse | None:
    """
    Get current email workflow configuration.

    Requires platform admin access.
    """
    service = EmailService(db)
    config = await service.get_config()

    if not config:
        return None

    return EmailWorkflowConfigResponse(
        workflow_id=config.workflow_id,
        workflow_name=config.workflow_name,
        is_configured=config.is_configured,
        configured_at=config.configured_at,
        configured_by=config.configured_by,
    )


@router.post("/config", status_code=status.HTTP_200_OK)
async def set_email_config(
    request: EmailWorkflowConfigRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> EmailWorkflowConfigResponse:
    """
    Set email workflow configuration.

    Validates the workflow has the correct signature before saving.
    Requires platform admin access.
    """
    service = EmailService(db)

    try:
        config = await service.save_config(
            workflow_id=request.workflow_id,
            updated_by=user.email,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    await db.commit()

    logger.info(f"Email workflow config set by {user.email}: {log_safe(request.workflow_id)}")

    return EmailWorkflowConfigResponse(
        workflow_id=config.workflow_id,
        workflow_name=config.workflow_name,
        is_configured=True,
        configured_at=config.configured_at,
        configured_by=config.configured_by,
    )


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_email_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """
    Delete email workflow configuration.

    Requires platform admin access.
    """
    service = EmailService(db)
    deleted = await service.delete_config()

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email configuration not found",
        )

    await db.commit()
    logger.info(f"Email config deleted by {user.email}")


@router.post("/validate/{workflow_id}")
async def validate_email_workflow(
    workflow_id: str,
    db: DbSession,
    user: CurrentActiveUser,
    request: EmailTestRequest | None = Body(default=None),
) -> EmailWorkflowValidationResponse:
    """
    Validate a workflow for use as email provider, optionally dispatching a test send.

    Checks that the workflow has the required signature:
    - Required: recipient (str), subject (str), body (str)
    - Optional: html_body (str | None)

    If `request.recipient` is set, also dispatches a real test message to that recipient
    via the configured email workflow.

    Requires platform admin access.
    """
    service = EmailService(db)
    result = await service.validate_workflow(workflow_id)

    response = EmailWorkflowValidationResponse(
        valid=result.valid,
        message=result.message,
        workflow_name=result.workflow_name,
        missing_params=result.missing_params,
        extra_required_params=result.extra_required_params,
        email_sent=False,
        send_error=None,
        execution_id=None,
    )

    if not result.valid or request is None or not request.recipient:
        return response

    send_result = await send_email(
        recipient=request.recipient,
        subject="Bifrost — email configuration test",
        body=(
            "This is a test message from Bifrost.\n\n"
            "If you received this, the configured email workflow is delivering messages correctly."
        ),
    )
    response.email_sent = send_result.success
    response.send_error = send_result.error
    response.execution_id = send_result.execution_id
    return response


# SDK-facing router for email operations (uses superuser auth, not admin auth)
sdk_router = APIRouter(
    prefix="/api/email",
    tags=["Email SDK"],
)


class EmailSendRequest(BaseModel):
    """Request to send an email via SDK."""
    recipient: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Plain text body")
    html_body: str | None = Field(default=None, description="Optional HTML body")
    scope: str | None = Field(default=None, description="Organization scope for config resolution")


class EmailSendResponse(BaseModel):
    """Response from email send."""
    success: bool
    execution_id: str | None = None
    error: str | None = None


@sdk_router.post("/send", response_model=EmailSendResponse)
async def send_email_sdk(
    request: EmailSendRequest,
    db: DbSession,
    user: CurrentSuperuser,
) -> EmailSendResponse:
    """
    Send an email via the configured email workflow.

    Used by SDK's email.send() method. Requires superuser auth (workflow engine token).
    """
    from uuid import uuid4
    from src.config import get_settings
    from src.sdk.context import ExecutionContext
    from src.services.execution.service import run_workflow

    # Get email config
    service = EmailService(db)
    config = await service.get_config()

    if not config:
        return EmailSendResponse(
            success=False,
            error="Email workflow not configured. Configure in Settings > Email.",
        )

    try:
        # Build execution context with caller's scope
        context = ExecutionContext(
            user_id="system",
            email="system@internal.gobifrost.com",
            name="Bifrost System",
            scope=request.scope or "GLOBAL",
            organization=None,
            is_platform_admin=True,
            is_function_key=False,
            execution_id=str(uuid4()),
            workflow_name="",
            public_url=get_settings().public_url,
        )

        result = await run_workflow(
            context=context,
            workflow_id=config.workflow_id,
            input_data={
                "recipient": request.recipient,
                "subject": request.subject,
                "body": request.body,
                "html_body": request.html_body,
            },
            transient=True,
            sync=True,
        )

        if result.status == "Success":
            return EmailSendResponse(
                success=True,
                execution_id=result.execution_id,
            )
        else:
            return EmailSendResponse(
                success=False,
                execution_id=result.execution_id,
                error=result.error or f"Email workflow failed with status: {result.status}",
            )

    except Exception as e:
        logger.exception(f"Error sending email to {log_safe(request.recipient)}")
        return EmailSendResponse(
            success=False,
            error=str(e),
        )
