"""Tests that manifest generation + serialization is deterministic.

Verifies that generate_manifest() → serialize_manifest_dir() produces
byte-identical output regardless of the order DB rows are returned.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.models.enums import AgentAccessLevel, FormAccessLevel


# Fixed UUIDs for reproducibility
ROLE_A = uuid4()
ROLE_B = uuid4()
ROLE_C = uuid4()
WF_ID = uuid4()
FORM_ID = uuid4()
AGENT_ID = uuid4()
APP_ID = uuid4()
EVENT_SOURCE_ID = uuid4()


def _mock_workflow(wf_id=WF_ID, name="det_wf"):
    wf = MagicMock()
    wf.id = wf_id
    wf.name = name
    wf.function_name = name
    wf.path = f"workflows/{name}.py"
    wf.type = "workflow"
    wf.organization_id = None
    wf.access_level = "role_based"
    wf.endpoint_enabled = False
    wf.timeout_seconds = 1800
    wf.public_endpoint = False
    wf.category = "General"
    wf.description = None
    wf.tags = []
    wf.is_active = True
    return wf


def _mock_form(form_id=FORM_ID, name="det_form"):
    form = MagicMock()
    form.id = form_id
    form.name = name
    form.organization_id = None
    form.access_level = FormAccessLevel.ROLE_BASED
    form.is_active = True
    # Inline content fields (set explicitly so MagicMock auto-attrs don't bleed in)
    form.description = None
    form.workflow_id = None
    form.launch_workflow_id = None
    form.default_launch_params = None
    form.allowed_query_params = None
    return form


def _mock_agent(agent_id=AGENT_ID, name="det_agent"):
    agent = MagicMock()
    agent.id = agent_id
    agent.name = name
    agent.organization_id = None
    agent.access_level = AgentAccessLevel.ROLE_BASED
    agent.is_active = True
    # Inline content fields
    agent.description = None
    agent.system_prompt = None
    agent.channels = []
    agent.knowledge_sources = []
    agent.system_tools = []
    agent.llm_model = None
    agent.llm_max_tokens = None
    agent.max_iterations = None
    agent.max_token_budget = None
    return agent


def _mock_app(app_id=APP_ID, name="det_app"):
    app = MagicMock()
    app.id = app_id
    app.name = name
    app.slug = name.lower()
    app.repo_path = f"apps/{app.slug}"
    app.organization_id = None
    app.access_level = "authenticated"
    app.description = None
    app.dependencies = None
    return app


def _mock_wf_role(wf_id, role_id):
    r = MagicMock()
    r.workflow_id = wf_id
    r.role_id = role_id
    return r


def _mock_form_role(form_id, role_id):
    r = MagicMock()
    r.form_id = form_id
    r.role_id = role_id
    return r


def _mock_agent_role(agent_id, role_id):
    r = MagicMock()
    r.agent_id = agent_id
    r.role_id = role_id
    return r


def _mock_app_role(app_id, role_id):
    r = MagicMock()
    r.app_id = app_id
    r.role_id = role_id
    return r


def _mock_event_source(source_id=EVENT_SOURCE_ID, name="det_source"):
    es = MagicMock()
    es.id = source_id
    es.name = name
    es.source_type = "schedule"
    es.organization_id = None
    es.is_active = True
    return es


def _mock_schedule_source(source_id=EVENT_SOURCE_ID):
    from src.models.enums import ScheduleOverlapPolicy
    ss = MagicMock()
    ss.event_source_id = source_id
    ss.cron_expression = "0 * * * *"
    ss.timezone = "UTC"
    ss.enabled = True
    ss.overlap_policy = ScheduleOverlapPolicy.SKIP
    return ss


def _make_result(items):
    """Create a mock query result from a list of items."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    result.scalars.return_value.unique.return_value.all.return_value = items
    return result



def _build_side_effects(
    *,
    wf_roles_order: list,
    form_roles_order: list,
    agent_roles_order: list,
    app_roles_order: list,
    mappings_order: list | None = None,
    subs_order: list | None = None,
):
    """Build a db.execute side_effect list with specified orderings.

    Order matches generate_manifest() query sequence exactly.
    """
    wf = _mock_workflow()
    form = _mock_form()
    agent = _mock_agent()
    app = _mock_app()
    es = _mock_event_source()
    ss = _mock_schedule_source()

    empty = _make_result([])

    return [
        _make_result([wf]),           # workflows
        _make_result([form]),         # forms
        _make_result([agent]),        # agents
        _make_result([app]),          # apps
        empty,                        # organizations
        empty,                        # roles
        _make_result(wf_roles_order),     # workflow_roles
        _make_result(form_roles_order),   # form_roles
        _make_result(agent_roles_order),  # agent_roles
        _make_result(app_roles_order),    # app_roles
        empty,                        # form_fields (inline form_schema)
        empty,                        # agent_tools (inline tool_ids)
        empty,                        # agent_delegations (inline delegated_agent_ids)
        empty,                        # agent_mcp_connections (inline mcp_connection_ids)
        empty,                        # integrations
        empty,                        # config_schemas
        empty,                        # oauth_providers
        _make_result(mappings_order or []),  # integration_mappings
        empty,                        # configs
        empty,                        # tables
        _make_result([es]),           # event_sources
        _make_result([ss]),           # schedule_sources
        empty,                        # webhook_sources
        _make_result(subs_order or []),   # event_subscriptions
        empty,                        # mcp_servers
        empty,                        # mcp_connections
        empty,                        # mcp_connection_tools
    ]


@pytest.mark.asyncio
async def test_role_order_does_not_affect_output():
    """Swapping role assignment order should produce identical YAML."""
    from src.services.manifest_generator import generate_manifest
    from bifrost.manifest import serialize_manifest_dir

    wf_r_a = _mock_wf_role(WF_ID, ROLE_A)
    wf_r_b = _mock_wf_role(WF_ID, ROLE_B)
    wf_r_c = _mock_wf_role(WF_ID, ROLE_C)

    form_r_a = _mock_form_role(FORM_ID, ROLE_A)
    form_r_b = _mock_form_role(FORM_ID, ROLE_B)

    agent_r_a = _mock_agent_role(AGENT_ID, ROLE_A)
    agent_r_b = _mock_agent_role(AGENT_ID, ROLE_B)

    app_r_a = _mock_app_role(APP_ID, ROLE_A)
    app_r_b = _mock_app_role(APP_ID, ROLE_B)

    # Order 1: A, B, C
    db1 = AsyncMock()
    db1.execute = AsyncMock(side_effect=_build_side_effects(
        wf_roles_order=[wf_r_a, wf_r_b, wf_r_c],
        form_roles_order=[form_r_a, form_r_b],
        agent_roles_order=[agent_r_a, agent_r_b],
        app_roles_order=[app_r_a, app_r_b],
    ))
    m1 = await generate_manifest(db1)
    files1 = serialize_manifest_dir(m1)

    # Order 2: C, B, A (reversed)
    db2 = AsyncMock()
    db2.execute = AsyncMock(side_effect=_build_side_effects(
        wf_roles_order=[wf_r_c, wf_r_b, wf_r_a],
        form_roles_order=[form_r_b, form_r_a],
        agent_roles_order=[agent_r_b, agent_r_a],
        app_roles_order=[app_r_b, app_r_a],
    ))
    m2 = await generate_manifest(db2)
    files2 = serialize_manifest_dir(m2)

    assert files1 == files2, "Role ordering should not affect serialized output"


def test_event_subscription_query_has_secondary_sort():
    """EventSubscription query should have a secondary sort key for determinism.

    Subscription order is enforced at the SQL level via order_by, which can't
    be tested with mocked results. Instead, verify that the query in the
    source code includes both event_source_id and workflow_id in order_by.
    """
    import inspect
    from src.services.manifest_generator import generate_manifest

    source = inspect.getsource(generate_manifest)
    # The query should have both sort columns
    assert "EventSubscription.event_source_id" in source
    assert "EventSubscription.workflow_id" in source


def test_integration_mapping_query_has_secondary_sort():
    """IntegrationMapping query should have a secondary sort key for determinism."""
    import inspect
    from src.services.manifest_generator import generate_manifest

    source = inspect.getsource(generate_manifest)
    assert "IntegrationMapping.integration_id" in source
    assert "IntegrationMapping.organization_id" in source


@pytest.mark.asyncio
async def test_full_manifest_idempotent_serialization():
    """Running generate + serialize twice with same data produces identical bytes."""
    from src.services.manifest_generator import generate_manifest
    from bifrost.manifest import serialize_manifest_dir

    wf_r_a = _mock_wf_role(WF_ID, ROLE_A)
    wf_r_b = _mock_wf_role(WF_ID, ROLE_B)

    results: list[dict[str, str]] = []
    for _ in range(3):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_build_side_effects(
            wf_roles_order=[wf_r_b, wf_r_a],
            form_roles_order=[],
            agent_roles_order=[],
            app_roles_order=[],
        ))
        m = await generate_manifest(db)
        results.append(serialize_manifest_dir(m))

    assert results[0] == results[1] == results[2], "Repeated serialization should be identical"
    assert "workflows.yaml" in results[0]
