"""List logo URLs stay usable while legacy thumbnail backfill drains."""

from types import SimpleNamespace
from datetime import datetime, timezone
from uuid import uuid4

from src.routers.agents import _agent_logo_url
from src.routers.applications import _application_logo_url
from shared.sdk_forms import attach_form_logo_fields, form_logo_url
from src.routers.integrations import _integration_logo_url, _integration_to_response
from src.models.contracts.forms import FormPublic


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


def test_legacy_integration_logo_uses_uncached_endpoint_during_backfill() -> None:
    integration = _entity(
        name="Logo Integration",
        has_oauth_config=False,
        is_deleted=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        logo_content_type="image/png",
    )
    assert _integration_logo_url(integration) == f"/api/integrations/{integration.id}/logo"


def test_legacy_form_logo_uses_uncached_endpoint_during_backfill() -> None:
    form = _entity(logo_content_type="image/png")
    assert form_logo_url(form) == f"/api/forms/{form.id}/logo"


def test_thumbnail_logo_keeps_immutable_versioned_url() -> None:
    agent = _entity(
        logo_content_type="image/png",
        logo_thumbnail_version="a" * 64,
    )
    assert _agent_logo_url(agent) == (
        f"/api/agents/{agent.id}/logo?v={'a' * 64}"
    )


def test_integration_response_includes_logo_url_and_connection_counts() -> None:
    integration = _entity(
        name="Counted Integration",
        description="Counts and connection state",
        has_oauth_config=True,
        is_deleted=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        list_entities_data_provider_id=None,
        config_schema=None,
        entity_id=None,
        entity_id_name=None,
        default_entity_id=None,
        logo_content_type="image/png",
        logo_thumbnail_version="b" * 64,
    )

    response = _integration_to_response(
        integration,
        summary=(4, 2, {"completed": 2, "failed": 1}),
    )

    assert response.logo is None
    assert response.description == "Counts and connection state"
    assert response.logo_url == f"/api/integrations/{integration.id}/logo?v={'b' * 64}"
    assert response.mapping_count == 4
    assert response.connected_count == 2
    assert response.needs_reconnection_count == 1
    assert response.connection_status_counts == {"completed": 2, "failed": 1}


def test_form_response_includes_logo_url_and_version() -> None:
    form = _entity(
        logo_content_type="image/png",
        logo_thumbnail_version="c" * 64,
    )
    response = FormPublic(
        id=form.id,
        name="Logo Form",
        is_active=True,
    )

    attach_form_logo_fields(response, form)

    assert response.logo is None
    assert response.logo_url == f"/api/forms/{form.id}/logo?v={'c' * 64}"
    assert response.logo_version == "c" * 64
