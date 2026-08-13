"""Tests for scoped required-instruction storage and composition."""

from uuid import uuid4

import pytest

from src.models.orm import Organization
from src.services.required_instructions import (
    MEMORY_INSTRUCTIONS,
    RequiredInstructionsService,
)


@pytest.mark.asyncio
async def test_required_instructions_compose_memory_global_and_organization(db_session):
    organization = Organization(
        id=uuid4(),
        name="Acme",
        created_by="admin@example.com",
    )
    db_session.add(organization)
    await db_session.flush()
    service = RequiredInstructionsService(
        db_session,
        organization_id=organization.id,
    )

    await service.set_configured(
        "Confirm destructive actions.",
        organization_id=None,
        updated_by="admin@example.com",
    )
    await service.set_configured(
        "Use the Acme onboarding runbook.",
        organization_id=organization.id,
        updated_by="admin@example.com",
    )

    assert await service.resolved(memory_enabled=True) == [
        f"# Memory\n\n{MEMORY_INSTRUCTIONS}",
        "# Global Instructions\n\nConfirm destructive actions.",
        "# Organization Instructions\n\nUse the Acme onboarding runbook.",
    ]


@pytest.mark.asyncio
async def test_required_instructions_omit_empty_sections_and_can_be_cleared(db_session):
    service = RequiredInstructionsService(db_session, organization_id=None)

    assert await service.resolved(memory_enabled=False) == []

    await service.set_configured(
        "  Keep responses concise.  ",
        organization_id=None,
        updated_by="admin@example.com",
    )
    assert await service.configured(None) == "Keep responses concise."

    await service.set_configured(
        "",
        organization_id=None,
        updated_by="admin@example.com",
    )
    assert await service.resolved(memory_enabled=False) == []
