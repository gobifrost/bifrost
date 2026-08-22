"""List logo URLs stay usable while legacy thumbnail backfill drains."""

from types import SimpleNamespace
from uuid import uuid4

from src.routers.agents import _agent_logo_url
from src.routers.applications import _application_logo_url


def _entity(**overrides):
    values = {
        "id": uuid4(),
        "logo_content_type": None,
        "logo_thumbnail_version": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_agent_logo_uses_uncached_endpoint_during_backfill() -> None:
    agent = _entity(logo_content_type="image/png")
    assert _agent_logo_url(agent) == f"/api/agents/{agent.id}/logo"


def test_legacy_application_logo_uses_uncached_endpoint_during_backfill() -> None:
    application = _entity(logo_content_type="image/svg+xml")
    assert _application_logo_url(application) == (
        f"/api/applications/{application.id}/logo"
    )


def test_thumbnail_logo_keeps_immutable_versioned_url() -> None:
    agent = _entity(
        logo_content_type="image/png",
        logo_thumbnail_version="a" * 64,
    )
    assert _agent_logo_url(agent) == (
        f"/api/agents/{agent.id}/logo?v={'a' * 64}"
    )
