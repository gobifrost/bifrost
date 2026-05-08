"""
Email Configuration Pydantic Models

Request/response models for email workflow configuration.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class EmailWorkflowConfigRequest(BaseModel):
    """Request to set the email workflow."""

    workflow_id: str = Field(
        ...,
        description="UUID of the workflow to use for sending emails",
    )


class EmailWorkflowConfigResponse(BaseModel):
    """Email workflow configuration response."""

    workflow_id: str
    workflow_name: str
    is_configured: bool = True
    configured_at: datetime | None = None
    configured_by: str | None = None


class EmailTestRequest(BaseModel):
    """Request to test an email workflow with an optional real send."""

    recipient: str | None = None  # None = signature validation only; set = real send


class EmailWorkflowValidationResponse(BaseModel):
    """Response from validating a workflow for email sending."""

    valid: bool
    message: str
    workflow_name: str | None = None
    missing_params: list[str] | None = None
    extra_required_params: list[str] | None = None
    email_sent: bool = False
    send_error: str | None = None
    execution_id: str | None = None
