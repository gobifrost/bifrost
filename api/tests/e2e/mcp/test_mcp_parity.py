"""E2E tests for the Task 6 MCP parity tools.

Covers the thin-wrapper surface added in
``docs/plans/2026-04-18-cli-mutation-surface-and-mcp-parity.md`` (lines
350-390):

* Roles: ``list_roles``, ``create_role``, ``update_role``, ``delete_role``.
* Configs: ``list_configs``, ``create_config``, ``update_config``,
  ``delete_config``.
* Integrations: ``create_integration``, ``update_integration``,
  ``add_integration_mapping``, ``update_integration_mapping``.
* Organizations: ``update_organization``, ``delete_organization``
  (``list`` / ``get`` / ``create`` already existed and are not touched).
* Workflow lifecycle: ``update_workflow``, ``delete_workflow``,
  ``grant_workflow_role``, ``revoke_workflow_role``
  (``list`` / ``register`` / ``execute`` already existed and are not touched).

Each tool is invoked directly (bypassing FastMCP transport) with a
``MockMCPContext`` that carries the platform admin's identity. The
``BIFROST_MCP_HTTP_BRIDGE_URL`` env var routes the tool's REST calls
through the running API container so writes land in the same test DB
``e2e_client`` reads from.

Also verifies that each parity tool's Python signature exposes every
writable DTO field (with documented renames) — a structural check
that the CLI and MCP surfaces stay in sync.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import sys
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from typing import AsyncIterator

# Standalone bifrost SDK package import (mirrors other CLI/MCP tests).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from bifrost.dto_flags import DTO_EXCLUDES  # noqa: E402
from tests.e2e.conftest import write_and_register  # noqa: E402


# =============================================================================
# Shared fixtures
# =============================================================================


class MockMCPContext:
    """Minimal MCP context for driving tool handlers in tests."""

    def __init__(
        self,
        user_id: str,
        user_email: str,
        is_platform_admin: bool = True,
        org_id: str | None = None,
        user_name: str = "E2E Admin",
    ):
        self.user_id = user_id
        self.user_email = user_email
        self.is_platform_admin = is_platform_admin
        self.org_id = org_id
        self.user_name = user_name
        self.accessible_namespaces: list[str] = []
        self.session = None


@pytest_asyncio.fixture
async def mcp_bridge_env(e2e_api_url) -> AsyncIterator[str]:
    """Point the parity tools' HTTP bridge at the running API container.

    The bridge falls back to in-process ASGITransport without this — but
    that won't share the real API's DB/Redis/object-storage state we need for
    end-to-end behaviour.
    """
    prev = os.environ.get("BIFROST_MCP_HTTP_BRIDGE_URL")
    os.environ["BIFROST_MCP_HTTP_BRIDGE_URL"] = e2e_api_url
    try:
        yield e2e_api_url
    finally:
        if prev is None:
            os.environ.pop("BIFROST_MCP_HTTP_BRIDGE_URL", None)
        else:
            os.environ["BIFROST_MCP_HTTP_BRIDGE_URL"] = prev


@pytest.fixture
def admin_context(platform_admin, mcp_bridge_env) -> MockMCPContext:
    """``MCPContext`` populated from the seeded platform admin."""
    return MockMCPContext(
        user_id=str(platform_admin.user_id) if platform_admin.user_id else "",
        user_email=platform_admin.email,
        is_platform_admin=True,
    )


@pytest.fixture
def org_context(org1_user, mcp_bridge_env) -> MockMCPContext:
    """Real organization user context for REST-equivalent MCP auth tests."""
    return MockMCPContext(
        user_id=str(org1_user.user_id),
        user_email=org1_user.email,
        is_platform_admin=False,
        org_id=str(org1_user.organization_id),
        user_name=org1_user.name,
    )


# =============================================================================
# Field-parity: MCP tool signature covers every writable DTO field
# =============================================================================

# Per-tool signature → DTO comparison spec.
#
# ``extra_args`` lists tool kwargs that are NOT writable DTO fields
# (typically ``*_ref`` lookup args used to identify the target entity, plus
# things like ``mapping_id`` and ``force_deactivation``). They are
# subtracted from the signature before comparing to the DTO.
#
# ``field_renames`` maps ``dto_field_name → tool_kwarg_name`` for the small
# set of intentional renames where the MCP tool exposes a different name
# than the DTO field (because ``assemble_body`` rewrites the wire payload
# or the field is a ref the tool resolves before sending). Adding to this
# map is a deliberate act — an unexpected divergence should leave the test
# failing so the drift is visible.
SIGNATURE_PARITY_SPECS: list[dict] = [
    {
        "model_path": "src.models.contracts.agents:AgentCreate",
        "tool_path": ("src.services.mcp_server.tools.agents:bifrost_create_agent"),
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.agents:AgentUpdate",
        "tool_path": ("src.services.mcp_server.tools.agents:bifrost_update_agent"),
        "extra_args": {"agent_ref", "scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.forms:FormCreate",
        "tool_path": "src.services.mcp_server.tools.forms:bifrost_create_form",
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.forms:FormUpdate",
        "tool_path": "src.services.mcp_server.tools.forms:bifrost_update_form",
        "extra_args": {"form_ref", "scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.tables:TableCreate",
        "tool_path": "src.services.mcp_server.tools.tables:bifrost_create_table",
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.tables:TableUpdate",
        "tool_path": "src.services.mcp_server.tools.tables:bifrost_update_table",
        "extra_args": {"table_ref", "scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.applications:ApplicationCreate",
        "tool_path": "src.services.mcp_server.tools.apps:bifrost_create_app",
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.applications:ApplicationUpdate",
        "tool_path": "src.services.mcp_server.tools.apps:bifrost_update_app",
        "extra_args": {"app_ref", "scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.events:EventSourceCreate",
        "tool_path": "src.services.mcp_server.tools.events:bifrost_create_event_source",
        "extra_args": {
            "scope",
            "adapter_name",
            "integration_id",
            "webhook_config",
            "rate_limit_per_minute",
            "rate_limit_window_seconds",
            "rate_limit_enabled",
            "cron_expression",
            "timezone",
            "schedule_enabled",
            "overlap_policy",
        },
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.events:EventSourceUpdate",
        "tool_path": "src.services.mcp_server.tools.events:bifrost_update_event_source",
        "extra_args": {
            "source_ref",
            "scope",
            "adapter_name",
            "integration_id",
            "webhook_config",
            "rate_limit_per_minute",
            "rate_limit_window_seconds",
            "rate_limit_enabled",
            "clear_webhook_integration",
            "clear_rate_limit",
            "cron_expression",
            "timezone",
            "schedule_enabled",
            "overlap_policy",
        },
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.events:EventSubscriptionCreate",
        "tool_path": "src.services.mcp_server.tools.events:bifrost_create_event_subscription",
        "extra_args": {"source_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.events:EventSubscriptionUpdate",
        "tool_path": "src.services.mcp_server.tools.events:bifrost_update_event_subscription",
        "extra_args": {
            "source_ref",
            "subscription_id",
            "clear_event_type",
            "clear_filter_expression",
            "clear_input_mapping",
        },
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.users:RoleCreate",
        "tool_path": "src.services.mcp_server.tools.roles:create_role",
        "extra_args": set(),
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.users:RoleUpdate",
        "tool_path": "src.services.mcp_server.tools.roles:update_role",
        "extra_args": {"role_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.config:ConfigCreate",
        "tool_path": "src.services.mcp_server.tools.configs:create_config",
        # ``organization_id`` is excluded from the DTO flags (CLI targets org via
        # the unified --org/--global standard), but the MCP create_config tool
        # exposes it as a tool-side REF input (a UUID/name string resolved via
        # RefResolver), not the raw DTO field — so it's an extra_arg here.
        "extra_args": {"organization_id"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.config:ConfigUpdate",
        "tool_path": "src.services.mcp_server.tools.configs:update_config",
        "extra_args": {"config_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.claims:CustomClaimCreate",
        "tool_path": "src.services.mcp_server.tools.claims:create_claim",
        # `scope` is an org-targeting query param, not a DTO field — mirrors
        # the same convention used by other org-scoped router endpoints.
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.claims:CustomClaimUpdate",
        "tool_path": "src.services.mcp_server.tools.claims:update_claim",
        "extra_args": {"name", "scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.organizations:OrganizationUpdate",
        "tool_path": (
            "src.services.mcp_server.tools.organizations:update_organization"
        ),
        "extra_args": {"organization_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.integrations:IntegrationCreate",
        "tool_path": ("src.services.mcp_server.tools.integrations:create_integration"),
        "extra_args": set(),
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.integrations:IntegrationUpdate",
        "tool_path": ("src.services.mcp_server.tools.integrations:update_integration"),
        "extra_args": {"integration_ref"},
        # ``list_entities_data_provider_id`` is a workflow ref the tool
        # accepts as a name/UUID/path::func and resolves to a UUID before
        # POSTing — it is exposed under the shorter ``_data_provider`` name.
        "field_renames": {
            "list_entities_data_provider_id": "list_entities_data_provider",
        },
    },
    {
        "model_path": ("src.models.contracts.integrations:IntegrationMappingCreate"),
        "tool_path": (
            "src.services.mcp_server.tools.integrations:add_integration_mapping"
        ),
        "extra_args": {"integration_ref"},
        # ``organization_id`` is a UUID on the DTO but the MCP tool accepts
        # an org ref (UUID or name), exposed as ``organization``.
        "field_renames": {"organization_id": "organization"},
    },
    {
        "model_path": ("src.models.contracts.integrations:IntegrationMappingUpdate"),
        "tool_path": (
            "src.services.mcp_server.tools.integrations:update_integration_mapping"
        ),
        "extra_args": {"integration_ref", "mapping_id"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.workflows:WorkflowUpdateRequest",
        "tool_path": "src.services.mcp_server.tools.workflow:update_workflow",
        "extra_args": {"workflow_ref"},
        "field_renames": {},
    },
]


def _import_attr(dotted: str):
    """Resolve a ``module:attr`` reference."""
    module_name, attr_name = dotted.split(":")
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


class TestMcpParitySchemas:
    """The MCP tool signature for each parity tool must match the DTO surface.

    This is a pure-Python introspection check — no API / DB required.
    Every non-excluded DTO field must appear as a parameter on the
    corresponding tool function (modulo documented renames in
    ``SIGNATURE_PARITY_SPECS``). Adding a new DTO field that the MCP tool
    doesn't expose fails this test loudly — the same way
    ``tests/unit/test_dto_flags.py`` catches CLI drift.
    """

    @pytest.mark.parametrize(
        "spec",
        SIGNATURE_PARITY_SPECS,
        ids=lambda s: s["tool_path"].rsplit(":", 1)[-1],
    )
    def test_signature_exposes_all_writable_fields(self, spec: dict) -> None:
        model_cls = _import_attr(spec["model_path"])
        tool_fn = _import_attr(spec["tool_path"])

        model_name = model_cls.__name__
        excludes = DTO_EXCLUDES.get(model_name, set())
        renames: dict[str, str] = spec["field_renames"]

        # Expected tool kwargs = (writable DTO fields − excludes), with any
        # renamed DTO field swapped for its tool-side name.
        expected: set[str] = set()
        for field_name in model_cls.model_fields:
            if field_name in excludes:
                continue
            expected.add(renames.get(field_name, field_name))

        # Actual tool kwargs = signature params minus ``context`` and
        # the per-tool ``extra_args`` (target refs / non-DTO kwargs).
        sig = inspect.signature(tool_fn)
        params = {
            name
            for name in sig.parameters
            if name != "context" and name not in spec["extra_args"]
        }

        missing = expected - params
        extra = params - expected
        assert not missing and not extra, (
            f"MCP tool {tool_fn.__name__} signature drifted from "
            f"{model_name}.\n"
            f"  declared DTO fields: {sorted(model_cls.model_fields)}\n"
            f"  excluded:            {sorted(excludes)}\n"
            f"  expected kwargs:     {sorted(expected)}\n"
            f"  signature kwargs:    {sorted(params)}\n"
            f"  missing kwargs:      {sorted(missing)}\n"
            f"  extra kwargs:        {sorted(extra)}\n"
            f"Either expose the new field on the MCP tool, add it to "
            f"DTO_EXCLUDES['{model_name}'], or document the rename in "
            f"SIGNATURE_PARITY_SPECS."
        )


# =============================================================================
# Agents
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityAgents:
    async def test_agents_crud_roundtrip_and_audit(
        self,
        admin_context,
        e2e_client,
        platform_admin,
    ) -> None:
        from src.services.mcp_server.tools.agents import (
            bifrost_create_agent,
            bifrost_delete_agent,
            bifrost_get_agent,
            bifrost_list_agents,
            bifrost_update_agent,
        )

        listed = await bifrost_list_agents(admin_context)
        assert listed.structured_content is not None
        assert listed.structured_content.get("count", -1) >= 0

        name = f"mcp-parity-agent-{uuid4().hex[:8]}"
        created_result = await bifrost_create_agent(
            admin_context,
            name=name,
            system_prompt="You verify Agent transport parity.",
            description="created through canonical MCP",
            access_level="authenticated",
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        agent_id = str(created["id"])

        from src.services.repo_storage import RepoStorage

        manifest_after_create = (
            await RepoStorage().read(".bifrost/agents.yaml")
        ).decode("utf-8")
        assert agent_id in manifest_after_create

        fetched_result = await bifrost_get_agent(admin_context, agent_ref=name)
        fetched = fetched_result.structured_content or {}
        assert fetched.get("id") == agent_id
        assert fetched.get("system_prompt") == "You verify Agent transport parity."

        renamed = f"mcp-parity-agent-renamed-{uuid4().hex[:8]}"
        updated_result = await bifrost_update_agent(
            admin_context,
            agent_ref=name,
            name=renamed,
            description="updated through canonical MCP",
        )
        updated = updated_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("name") == renamed

        rest_get = e2e_client.get(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
        )
        assert rest_get.status_code == 200, rest_get.text
        assert rest_get.json()["description"] == "updated through canonical MCP"

        deleted_result = await bifrost_delete_agent(
            admin_context,
            agent_ref=renamed,
        )
        deleted = deleted_result.structured_content or {}
        assert deleted.get("deleted") == agent_id
        assert (
            e2e_client.get(
                f"/api/agents/{agent_id}",
                headers=platform_admin.headers,
            ).status_code
            == 404
        )
        manifest_paths = await RepoStorage().list(".bifrost/")
        if ".bifrost/agents.yaml" in manifest_paths:
            manifest_after_delete = (
                await RepoStorage().read(".bifrost/agents.yaml")
            ).decode("utf-8")
            assert agent_id not in manifest_after_delete

        audit = e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={"action": "agent.", "resource_type": "agent", "limit": 50},
        )
        assert audit.status_code == 200, audit.text
        actions = {
            entry["action"]
            for entry in audit.json()["entries"]
            if entry["resource_id"] == agent_id
        }
        assert actions == {"agent.create", "agent.update", "agent.delete"}

    async def test_private_agent_authorization_matches_rest(
        self,
        admin_context,
        org_context,
        org2,
    ) -> None:
        from src.services.mcp_server.tools.agents import (
            bifrost_create_agent,
            bifrost_delete_agent,
            bifrost_get_agent,
            bifrost_update_agent,
        )

        forbidden_create = await bifrost_create_agent(
            org_context,
            name=f"forbidden-agent-{uuid4().hex[:8]}",
            system_prompt="This must not be created.",
            access_level="authenticated",
        )
        assert "error" in (forbidden_create.structured_content or {})

        name = f"private-mcp-agent-{uuid4().hex[:8]}"
        private_result = await bifrost_create_agent(
            org_context,
            name=name,
            system_prompt="Private Agent instructions.",
            access_level="private",
        )
        private_agent = private_result.structured_content or {}
        assert "error" not in private_agent, private_agent
        private_id = str(private_agent["id"])
        foreign_id: str | None = None
        try:
            assert private_agent["organization_id"] == str(org_context.org_id)
            assert private_agent["owner_user_id"] == str(org_context.user_id)

            updated = await bifrost_update_agent(
                org_context,
                agent_ref=name,
                description="owner update",
            )
            assert (updated.structured_content or {}).get("description") == (
                "owner update"
            )

            foreign_name = f"foreign-mcp-agent-{uuid4().hex[:8]}"
            foreign_result = await bifrost_create_agent(
                admin_context,
                name=foreign_name,
                system_prompt="Foreign organization Agent.",
                access_level="authenticated",
                scope=str(org2["id"]),
            )
            foreign = foreign_result.structured_content or {}
            assert "error" not in foreign, foreign
            foreign_id = str(foreign["id"])
            forbidden_get = await bifrost_get_agent(
                org_context,
                agent_ref=foreign_id,
            )
            assert "error" in (forbidden_get.structured_content or {})
        finally:
            if foreign_id is not None:
                deleted_foreign = await bifrost_delete_agent(
                    admin_context,
                    agent_ref=foreign_id,
                )
                assert (deleted_foreign.structured_content or {}).get(
                    "deleted"
                ) == foreign_id
            deleted_private = await bifrost_delete_agent(
                org_context,
                agent_ref=private_id,
            )
            assert (deleted_private.structured_content or {}).get(
                "deleted"
            ) == private_id


# =============================================================================
# Forms
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityForms:
    async def test_forms_crud_roundtrip_manifest_and_audit(
        self,
        admin_context,
        e2e_client,
        platform_admin,
    ) -> None:
        from src.services.mcp_server.tools.forms import (
            bifrost_create_form,
            bifrost_delete_form,
            bifrost_get_form,
            bifrost_list_forms,
            bifrost_update_form,
        )
        from src.services.repo_storage import RepoStorage

        listed = await bifrost_list_forms(admin_context)
        assert listed.structured_content is not None
        assert listed.structured_content.get("count", -1) >= 0

        name = f"mcp-parity-form-{uuid4().hex[:8]}"
        created_result = await bifrost_create_form(
            admin_context,
            name=name,
            description="created through canonical MCP",
            form_schema={
                "fields": [
                    {
                        "name": "summary",
                        "type": "text",
                        "label": "Summary",
                        "required": True,
                    }
                ]
            },
            access_level="authenticated",
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        form_id = str(created["id"])

        manifest_after_create = (
            await RepoStorage().read(".bifrost/forms.yaml")
        ).decode("utf-8")
        assert form_id in manifest_after_create

        fetched_result = await bifrost_get_form(admin_context, form_ref=name)
        fetched = fetched_result.structured_content or {}
        assert fetched.get("id") == form_id
        assert fetched["form_schema"]["fields"][0]["name"] == "summary"

        renamed = f"mcp-parity-form-renamed-{uuid4().hex[:8]}"
        updated_result = await bifrost_update_form(
            admin_context,
            form_ref=name,
            name=renamed,
            description="updated through canonical MCP",
            form_schema={
                "fields": [
                    {
                        "name": "details",
                        "type": "textarea",
                        "label": "Details",
                    }
                ]
            },
        )
        updated = updated_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("name") == renamed
        assert updated["form_schema"]["fields"][0]["name"] == "details"

        rest_get = e2e_client.get(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        assert rest_get.status_code == 200, rest_get.text
        assert rest_get.json()["description"] == "updated through canonical MCP"

        deactivated_result = await bifrost_delete_form(
            admin_context,
            form_ref=renamed,
        )
        deactivated = deactivated_result.structured_content or {}
        assert deactivated == {"deleted": form_id, "purged": False}
        inactive = e2e_client.get(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        assert inactive.status_code == 200, inactive.text
        assert inactive.json()["is_active"] is False

        manifest_paths = await RepoStorage().list(".bifrost/")
        if ".bifrost/forms.yaml" in manifest_paths:
            manifest_after_delete = (
                await RepoStorage().read(".bifrost/forms.yaml")
            ).decode("utf-8")
            assert form_id not in manifest_after_delete

        purged_result = await bifrost_delete_form(
            admin_context,
            form_ref=form_id,
            purge=True,
        )
        purged = purged_result.structured_content or {}
        assert purged == {"deleted": form_id, "purged": True}
        assert (
            e2e_client.get(
                f"/api/forms/{form_id}",
                headers=platform_admin.headers,
            ).status_code
            == 404
        )

        audit = e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={"action": "form.", "resource_type": "form", "limit": 50},
        )
        assert audit.status_code == 200, audit.text
        entries = [
            entry
            for entry in audit.json()["entries"]
            if entry["resource_id"] == form_id
        ]
        assert {entry["action"] for entry in entries} == {
            "form.create",
            "form.update",
            "form.delete",
        }
        assert sum(entry["action"] == "form.delete" for entry in entries) == 2

    async def test_form_authorization_matches_rest(
        self,
        admin_context,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.forms import (
            bifrost_create_form,
            bifrost_delete_form,
            bifrost_get_form,
            bifrost_list_forms,
            bifrost_update_form,
        )

        forbidden_create = await bifrost_create_form(
            org_context,
            name=f"forbidden-form-{uuid4().hex[:8]}",
            form_schema={"fields": []},
        )
        assert "error" in (forbidden_create.structured_content or {})

        name = f"shared-mcp-form-{uuid4().hex[:8]}"
        created_result = await bifrost_create_form(
            admin_context,
            name=name,
            form_schema={"fields": []},
            access_level="authenticated",
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        form_id = str(created["id"])
        try:
            listed = await bifrost_list_forms(org_context)
            visible_ids = {
                str(form["id"])
                for form in (listed.structured_content or {}).get("forms", [])
            }
            assert form_id in visible_ids

            fetched = await bifrost_get_form(org_context, form_ref=name)
            assert (fetched.structured_content or {}).get("id") == form_id

            forbidden_update = await bifrost_update_form(
                org_context,
                form_ref=form_id,
                description="must not persist",
            )
            assert "error" in (forbidden_update.structured_content or {})

            forbidden_delete = await bifrost_delete_form(
                org_context,
                form_ref=form_id,
            )
            assert "error" in (forbidden_delete.structured_content or {})
        finally:
            deactivated = await bifrost_delete_form(
                admin_context,
                form_ref=form_id,
            )
            assert (deactivated.structured_content or {}).get("deleted") == form_id
            purged = await bifrost_delete_form(
                admin_context,
                form_ref=form_id,
                purge=True,
            )
            assert (purged.structured_content or {}).get("purged") is True

    async def test_form_name_ambiguity_requires_uuid(
        self,
        admin_context,
        org_context,
    ) -> None:
        """CLI and MCP share strict ambiguity handling across visible scopes."""
        from src.services.mcp_server.tools.forms import (
            bifrost_create_form,
            bifrost_delete_form,
            bifrost_get_form,
        )

        name = f"ambiguous-mcp-form-{uuid4().hex[:8]}"
        created_ids: list[str] = []
        try:
            for scope in ("global", str(org_context.org_id)):
                created_result = await bifrost_create_form(
                    admin_context,
                    name=name,
                    form_schema={"fields": []},
                    access_level="authenticated",
                    scope=scope,
                )
                created = created_result.structured_content or {}
                assert "error" not in created, created
                created_ids.append(str(created["id"]))

            ambiguous_result = await bifrost_get_form(
                org_context,
                form_ref=name,
            )
            ambiguous = ambiguous_result.structured_content or {}
            assert "error" in ambiguous
            assert ambiguous["kind"] == "form"
            assert {candidate["uuid"] for candidate in ambiguous["candidates"]} == set(
                created_ids
            )

            for form_id in created_ids:
                fetched = await bifrost_get_form(org_context, form_ref=form_id)
                assert (fetched.structured_content or {}).get("id") == form_id
        finally:
            for form_id in created_ids:
                deactivated = await bifrost_delete_form(
                    admin_context,
                    form_ref=form_id,
                )
                assert (deactivated.structured_content or {}).get("deleted") == form_id
                purged = await bifrost_delete_form(
                    admin_context,
                    form_ref=form_id,
                    purge=True,
                )
                assert (purged.structured_content or {}).get("purged") is True


# =============================================================================
# Tables
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityTables:
    async def test_tables_crud_roundtrip_manifest_and_audit(
        self,
        admin_context,
        e2e_client,
        platform_admin,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.tables import (
            bifrost_create_table,
            bifrost_delete_table,
            bifrost_get_table,
            bifrost_list_tables,
            bifrost_update_table,
        )
        from src.services.repo_storage import RepoStorage

        listed = await bifrost_list_tables(admin_context)
        assert listed.structured_content is not None
        assert listed.structured_content.get("count", -1) >= 0

        name = f"mcp_parity_table_{uuid4().hex[:8]}"
        created_result = await bifrost_create_table(
            admin_context,
            name=name,
            description="created through canonical MCP",
            schema={"columns": [{"name": "summary", "type": "string"}]},
            policies={
                "policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    }
                ]
            },
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        table_id = str(created["id"])

        manifest_after_create = (
            await RepoStorage().read(".bifrost/tables.yaml")
        ).decode("utf-8")
        assert table_id in manifest_after_create

        fetched_result = await bifrost_get_table(admin_context, table_ref=name)
        fetched = fetched_result.structured_content or {}
        assert fetched.get("id") == table_id
        assert fetched["schema"]["columns"][0]["name"] == "summary"

        renamed = f"mcp_parity_table_renamed_{uuid4().hex[:8]}"
        updated_result = await bifrost_update_table(
            admin_context,
            table_ref=name,
            name=renamed,
            description="updated through canonical MCP",
            schema={"columns": [{"name": "details", "type": "string"}]},
            policies={"policies": []},
            scope=str(org_context.org_id),
        )
        updated = updated_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("name") == renamed
        assert updated.get("organization_id") == str(org_context.org_id)
        assert updated["schema"]["columns"][0]["name"] == "details"
        assert updated["policies"] == {"policies": []}

        rest_get = e2e_client.get(
            f"/api/tables/{table_id}",
            headers=platform_admin.headers,
        )
        assert rest_get.status_code == 200, rest_get.text
        assert rest_get.json()["description"] == "updated through canonical MCP"

        deleted_result = await bifrost_delete_table(
            admin_context,
            table_ref=renamed,
        )
        deleted = deleted_result.structured_content or {}
        assert deleted == {"success": True, "id": table_id}
        assert (
            e2e_client.get(
                f"/api/tables/{table_id}",
                headers=platform_admin.headers,
            ).status_code
            == 404
        )

        manifest_paths = await RepoStorage().list(".bifrost/")
        if ".bifrost/tables.yaml" in manifest_paths:
            manifest_after_delete = (
                await RepoStorage().read(".bifrost/tables.yaml")
            ).decode("utf-8")
            assert table_id not in manifest_after_delete

        audit = e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={"action": "table.", "resource_type": "table", "limit": 50},
        )
        assert audit.status_code == 200, audit.text
        entries = [
            entry
            for entry in audit.json()["entries"]
            if entry["resource_id"] == table_id
        ]
        assert {entry["action"] for entry in entries} == {
            "table.create",
            "table.update",
            "table.delete",
        }

    async def test_table_authorization_matches_rest(
        self,
        admin_context,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.tables import (
            bifrost_create_table,
            bifrost_delete_table,
            bifrost_get_table,
            bifrost_list_tables,
            bifrost_update_table,
        )

        forbidden_create = await bifrost_create_table(
            org_context,
            name=f"forbidden_table_{uuid4().hex[:8]}",
        )
        assert "error" in (forbidden_create.structured_content or {})

        name = f"admin_table_{uuid4().hex[:8]}"
        created_result = await bifrost_create_table(
            admin_context,
            name=name,
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        table_id = str(created["id"])
        try:
            assert "error" in (
                (await bifrost_list_tables(org_context)).structured_content or {}
            )
            assert "error" in (
                (
                    await bifrost_get_table(org_context, table_ref=table_id)
                ).structured_content
                or {}
            )
            assert "error" in (
                (
                    await bifrost_update_table(
                        org_context,
                        table_ref=table_id,
                        description="must not persist",
                    )
                ).structured_content
                or {}
            )
            assert "error" in (
                (
                    await bifrost_delete_table(org_context, table_ref=table_id)
                ).structured_content
                or {}
            )
        finally:
            deleted = await bifrost_delete_table(admin_context, table_ref=table_id)
            assert (deleted.structured_content or {}).get("id") == table_id

    async def test_table_name_ambiguity_requires_uuid(
        self,
        admin_context,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.tables import (
            bifrost_create_table,
            bifrost_delete_table,
            bifrost_get_table,
        )

        name = f"ambiguous_table_{uuid4().hex[:8]}"
        created_ids: list[str] = []
        try:
            for scope in ("global", str(org_context.org_id)):
                created_result = await bifrost_create_table(
                    admin_context,
                    name=name,
                    scope=scope,
                )
                created = created_result.structured_content or {}
                assert "error" not in created, created
                created_ids.append(str(created["id"]))

            ambiguous_result = await bifrost_get_table(
                admin_context,
                table_ref=name,
            )
            ambiguous = ambiguous_result.structured_content or {}
            assert "error" in ambiguous
            assert ambiguous["kind"] == "table"
            assert {candidate["uuid"] for candidate in ambiguous["candidates"]} == set(
                created_ids
            )

            for table_id in created_ids:
                fetched = await bifrost_get_table(admin_context, table_ref=table_id)
                assert (fetched.structured_content or {}).get("id") == table_id
        finally:
            for table_id in created_ids:
                deleted = await bifrost_delete_table(
                    admin_context,
                    table_ref=table_id,
                )
                assert (deleted.structured_content or {}).get("id") == table_id


# =============================================================================
# Applications
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityApplications:
    async def test_apps_roundtrip_dependencies_validation_manifest_and_audit(
        self,
        admin_context,
        e2e_client,
        platform_admin,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.apps import (
            bifrost_create_app,
            bifrost_delete_app,
            bifrost_get_app,
            bifrost_get_app_dependencies,
            bifrost_list_apps,
            bifrost_update_app,
            bifrost_update_app_dependencies,
            bifrost_validate_app,
        )
        from src.services.repo_storage import RepoStorage

        listed = await bifrost_list_apps(admin_context)
        assert listed.structured_content is not None
        assert listed.structured_content.get("count", -1) >= 0

        slug = f"mcp-parity-app-{uuid4().hex[:8]}"
        created_result = await bifrost_create_app(
            admin_context,
            name="MCP parity App",
            slug=slug,
            description="created through canonical MCP",
            access_level="authenticated",
            app_model="inline_v1",
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        app_id = str(created["id"])

        manifest_after_create = (await RepoStorage().read(".bifrost/apps.yaml")).decode(
            "utf-8"
        )
        assert app_id in manifest_after_create

        fetched_result = await bifrost_get_app(admin_context, app_ref=slug)
        fetched = fetched_result.structured_content or {}
        assert fetched.get("id") == app_id
        assert fetched.get("repo_path") == f"apps/{slug}"

        dependencies = {"lodash": "^4.17.21"}
        dependency_update = await bifrost_update_app_dependencies(
            admin_context,
            app_ref=slug,
            dependencies=dependencies,
        )
        assert (dependency_update.structured_content or {}).get(
            "dependencies"
        ) == dependencies
        dependency_get = await bifrost_get_app_dependencies(
            admin_context,
            app_ref=app_id,
        )
        assert (dependency_get.structured_content or {}).get(
            "dependencies"
        ) == dependencies

        validation_result = await bifrost_validate_app(
            admin_context,
            app_ref=app_id,
        )
        validation = validation_result.structured_content or {}
        assert "error" not in validation, validation
        assert isinstance(validation.get("valid"), bool)
        assert isinstance(validation.get("errors"), list)
        assert isinstance(validation.get("warnings"), list)

        renamed = f"mcp-parity-app-renamed-{uuid4().hex[:8]}"
        updated_result = await bifrost_update_app(
            admin_context,
            app_ref=slug,
            name="MCP parity App updated",
            slug=renamed,
            description="updated through canonical MCP",
            scope=str(org_context.org_id),
        )
        updated = updated_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("slug") == renamed
        assert updated.get("organization_id") == str(org_context.org_id)

        rest_get = e2e_client.get(
            f"/api/applications/{renamed}",
            headers=platform_admin.headers,
        )
        assert rest_get.status_code == 200, rest_get.text
        assert rest_get.json()["description"] == "updated through canonical MCP"

        deleted_result = await bifrost_delete_app(
            admin_context,
            app_ref=renamed,
        )
        deleted = deleted_result.structured_content or {}
        assert deleted == {"success": True, "id": app_id}
        assert (
            e2e_client.get(
                f"/api/applications/{renamed}",
                headers=platform_admin.headers,
            ).status_code
            == 404
        )

        manifest_paths = await RepoStorage().list(".bifrost/")
        if ".bifrost/apps.yaml" in manifest_paths:
            manifest_after_delete = (
                await RepoStorage().read(".bifrost/apps.yaml")
            ).decode("utf-8")
            assert app_id not in manifest_after_delete

        audit = e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={"action": "app.", "resource_type": "application", "limit": 50},
        )
        assert audit.status_code == 200, audit.text
        actions = {
            entry["action"]
            for entry in audit.json()["entries"]
            if entry["resource_id"] == app_id
        }
        assert actions == {
            "app.create",
            "app.update",
            "app.dependencies.update",
            "app.delete",
        }

    async def test_global_app_management_authorization_matches_rest(
        self,
        admin_context,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.apps import (
            bifrost_create_app,
            bifrost_delete_app,
            bifrost_get_app,
            bifrost_update_app,
            bifrost_update_app_dependencies,
        )

        slug = f"global-mcp-app-{uuid4().hex[:8]}"
        created_result = await bifrost_create_app(
            admin_context,
            name="Global MCP App",
            slug=slug,
            app_model="inline_v1",
            scope="global",
        )
        created = created_result.structured_content or {}
        assert "error" not in created, created
        app_id = str(created["id"])
        try:
            visible = await bifrost_get_app(org_context, app_ref=slug)
            assert (visible.structured_content or {}).get("id") == app_id

            forbidden_update = await bifrost_update_app(
                org_context,
                app_ref=app_id,
                description="must not persist",
            )
            assert "error" in (forbidden_update.structured_content or {})

            forbidden_dependencies = await bifrost_update_app_dependencies(
                org_context,
                app_ref=app_id,
                dependencies={"lodash": "^4.17.21"},
            )
            assert "error" in (forbidden_dependencies.structured_content or {})

            forbidden_delete = await bifrost_delete_app(
                org_context,
                app_ref=app_id,
            )
            assert "error" in (forbidden_delete.structured_content or {})
        finally:
            deleted = await bifrost_delete_app(admin_context, app_ref=app_id)
            assert (deleted.structured_content or {}).get("id") == app_id


# =============================================================================
# Events
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityEvents:
    async def test_event_source_and_subscription_roundtrip_manifest_and_audit(
        self,
        admin_context,
        e2e_client,
        platform_admin,
        org_context,
    ) -> None:
        from src.services.mcp_server.tools.events import (
            bifrost_create_event_source,
            bifrost_create_event_subscription,
            bifrost_delete_event_source,
            bifrost_delete_event_subscription,
            bifrost_get_event_source,
            bifrost_get_event_subscription,
            bifrost_list_event_sources,
            bifrost_list_event_subscriptions,
            bifrost_list_event_webhook_adapters,
            bifrost_update_event_source,
            bifrost_update_event_subscription,
        )
        from src.services.repo_storage import RepoStorage

        suffix = uuid4().hex[:8]
        workflow_path = f"workflows/mcp_event_parity_{suffix}.py"
        function_name = f"mcp_event_parity_{suffix}"
        workflow = write_and_register(
            e2e_client,
            platform_admin.headers,
            path=workflow_path,
            content=(
                "from bifrost import workflow\n\n"
                f"@workflow\nasync def {function_name}(event: dict) -> dict:\n"
                "    return {'received': event}\n"
            ),
            function_name=function_name,
            organization_id=None,
        )
        workflow_ref = f"{workflow_path}::{function_name}"
        source_name = f"mcp-event-parity-{suffix}"
        source_id: str | None = None
        subscription_id: str | None = None
        audit_source_id: str | None = None
        audit_subscription_id: str | None = None
        try:
            adapters = await bifrost_list_event_webhook_adapters(admin_context)
            assert any(
                adapter["name"] == "generic"
                for adapter in (adapters.structured_content or {}).get("adapters", [])
            )

            created_result = await bifrost_create_event_source(
                admin_context,
                name=source_name,
                source_type="schedule",
                cron_expression="0 9 * * *",
                timezone="America/New_York",
                overlap_policy="queue",
                scope="global",
            )
            created = created_result.structured_content or {}
            assert "error" not in created, created
            source_id = str(created["id"])
            audit_source_id = source_id
            assert created["schedule"]["overlap_policy"] == "queue"

            listed = await bifrost_list_event_sources(
                admin_context,
                source_type="schedule",
                scope="global",
            )
            assert source_id in {
                str(item["id"])
                for item in (listed.structured_content or {}).get("items", [])
            }
            fetched = await bifrost_get_event_source(
                admin_context,
                source_ref=source_name,
            )
            assert (fetched.structured_content or {}).get("id") == source_id

            manifest = (await RepoStorage().read(".bifrost/events.yaml")).decode(
                "utf-8"
            )
            assert source_id in manifest

            subscribed_result = await bifrost_create_event_subscription(
                admin_context,
                source_ref=source_name,
                workflow_id=workflow_ref,
                event_type="daily.report",
                input_mapping={"report_type": "daily"},
            )
            subscribed = subscribed_result.structured_content or {}
            assert "error" not in subscribed, subscribed
            subscription_id = str(subscribed["id"])
            audit_subscription_id = subscription_id
            assert str(subscribed["workflow_id"]) == str(workflow["id"])

            duplicate = await bifrost_create_event_subscription(
                admin_context,
                source_ref=source_id,
                workflow_id=workflow_ref,
            )
            assert (duplicate.structured_content or {}).get("status_code") == 409

            fetched_subscription = await bifrost_get_event_subscription(
                admin_context,
                source_ref=source_id,
                subscription_id=subscription_id,
            )
            assert (fetched_subscription.structured_content or {}).get(
                "id"
            ) == subscription_id
            subscriptions = await bifrost_list_event_subscriptions(
                admin_context,
                source_ref=source_id,
            )
            assert subscription_id in {
                str(item["id"])
                for item in (subscriptions.structured_content or {}).get("items", [])
            }

            updated_subscription = await bifrost_update_event_subscription(
                admin_context,
                source_ref=source_id,
                subscription_id=subscription_id,
                event_type="daily.rollup",
                is_active=False,
            )
            assert (updated_subscription.structured_content or {}).get(
                "event_type"
            ) == "daily.rollup"

            cleared_subscription = await bifrost_update_event_subscription(
                admin_context,
                source_ref=source_id,
                subscription_id=subscription_id,
                clear_event_type=True,
                clear_input_mapping=True,
            )
            cleared_subscription_body = cleared_subscription.structured_content or {}
            assert cleared_subscription_body.get("event_type") is None
            assert cleared_subscription_body.get("input_mapping") is None

            updated_source = await bifrost_update_event_source(
                admin_context,
                source_ref=source_id,
                name=f"{source_name}-updated",
                scope=str(org_context.org_id),
                cron_expression="30 9 * * *",
            )
            updated_source_body = updated_source.structured_content or {}
            assert updated_source_body.get("organization_id") == str(
                org_context.org_id
            )
            assert updated_source_body["schedule"]["cron_expression"] == (
                "30 9 * * *"
            )

            rest_source = e2e_client.get(
                f"/api/events/sources/{source_id}",
                headers=platform_admin.headers,
            )
            assert rest_source.status_code == 200, rest_source.text
            assert rest_source.json()["name"] == f"{source_name}-updated"

            denied = await bifrost_list_event_sources(org_context)
            assert "error" in (denied.structured_content or {})

            deleted_subscription = await bifrost_delete_event_subscription(
                admin_context,
                source_ref=source_id,
                subscription_id=subscription_id,
            )
            assert (deleted_subscription.structured_content or {}).get(
                "id"
            ) == subscription_id
            subscription_id = None

            deleted_source = await bifrost_delete_event_source(
                admin_context,
                source_ref=source_id,
            )
            assert (deleted_source.structured_content or {}).get("id") == source_id
            source_id = None

            manifest_paths = await RepoStorage().list(".bifrost/")
            if ".bifrost/events.yaml" in manifest_paths:
                after_delete = (
                    await RepoStorage().read(".bifrost/events.yaml")
                ).decode("utf-8")
                assert source_id not in after_delete

            audit = e2e_client.get(
                "/api/audit",
                headers=platform_admin.headers,
                params={"action": "event_", "limit": 50},
            )
            assert audit.status_code == 200, audit.text
            actions = {
                entry["action"]
                for entry in audit.json()["entries"]
                if entry["resource_id"] in {
                    audit_source_id,
                    audit_subscription_id,
                }
            }
            assert actions == {
                "event_source.create",
                "event_source.update",
                "event_source.delete",
                "event_subscription.create",
                "event_subscription.update",
                "event_subscription.delete",
            }
        finally:
            if subscription_id is not None and source_id is not None:
                await bifrost_delete_event_subscription(
                    admin_context,
                    source_ref=source_id,
                    subscription_id=subscription_id,
                )
            if source_id is not None:
                await bifrost_delete_event_source(
                    admin_context,
                    source_ref=source_id,
                )
            e2e_client.delete(
                f"/api/files/editor?path={workflow_path}",
                headers=platform_admin.headers,
            )


# =============================================================================
# Roles
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityRoles:
    async def test_get_role_by_uuid(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        """``get_role`` thin-wrapper round-trips a created role via UUID ref."""
        from src.services.mcp_server.tools.roles import get_role

        name = f"mcp-parity-get-role-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": name, "permissions": {"workflows.read": True}},
        )
        assert create_resp.status_code == 201, create_resp.text
        role_id = create_resp.json()["id"]

        try:
            result = await get_role(admin_context, role_ref=role_id)
            payload = result.structured_content or {}
            assert "error" not in payload, payload
            assert str(payload.get("id")) == str(role_id)
            assert payload.get("name") == name
        finally:
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)

    async def test_roles_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.roles import (
            create_role,
            delete_role,
            list_roles,
            update_role,
        )

        # list
        list_result = await list_roles(admin_context)
        assert list_result.structured_content is not None
        assert list_result.structured_content.get("count", -1) >= 0

        # create
        name = f"mcp-parity-role-{uuid4().hex[:8]}"
        perms = {"workflows.read": True}
        create_result = await create_role(
            admin_context,
            name=name,
            description="created by test_mcp_parity",
            permissions=perms,
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        role_id = str(created["id"])

        # update (by name ref)
        renamed = f"mcp-parity-role-renamed-{uuid4().hex[:8]}"
        update_result = await update_role(
            admin_context,
            role_ref=name,
            name=renamed,
            permissions={"workflows.read": True, "workflows.write": True},
        )
        updated = update_result.structured_content or {}
        assert updated.get("name") == renamed

        # Confirm via REST.
        get_resp = e2e_client.get(
            f"/api/roles/{role_id}", headers=platform_admin.headers
        )
        assert get_resp.status_code == 200

        # delete (by renamed ref)
        delete_result = await delete_role(admin_context, role_ref=renamed)
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == role_id
        get_after = e2e_client.get(
            f"/api/roles/{role_id}", headers=platform_admin.headers
        )
        assert get_after.status_code == 404


# =============================================================================
# Configs
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityConfigs:
    async def test_get_config_by_uuid(self, admin_context) -> None:
        """``get_config`` round-trips a created config via UUID ref.

        The server has no per-id GET endpoint for configs; the tool resolves
        the ref then locates the row in the list payload.
        """
        from src.services.mcp_server.tools.configs import (
            create_config,
            delete_config,
            get_config,
        )

        key = f"mcp_parity_get_{uuid4().hex[:8]}"
        create_result = await create_config(
            admin_context,
            key=key,
            value="hello",
            config_type="string",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        config_id = str(created["id"])

        try:
            result = await get_config(admin_context, config_ref=config_id)
            payload = result.structured_content or {}
            assert "error" not in payload, payload
            assert str(payload.get("id")) == config_id
            assert payload.get("key") == key
            assert payload.get("value") == "hello"
        finally:
            await delete_config(admin_context, config_ref=config_id)

    async def test_configs_crud_roundtrip(self, admin_context) -> None:
        from src.services.mcp_server.tools.configs import (
            create_config,
            delete_config,
            list_configs,
            update_config,
        )

        # list
        list_result = await list_configs(admin_context)
        assert list_result.structured_content is not None

        # create (global, plain string type via config_type)
        key = f"mcp_parity_{uuid4().hex[:8]}"
        create_result = await create_config(
            admin_context,
            key=key,
            value="initial",
            config_type="string",
            description="created by test_mcp_parity",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        config_id = str(created["id"])

        # update value by UUID ref
        update_result = await update_config(
            admin_context,
            config_ref=config_id,
            value="updated",
        )
        assert update_result.structured_content is not None
        assert "error" not in update_result.structured_content

        # delete by UUID
        delete_result = await delete_config(admin_context, config_ref=config_id)
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == config_id


# =============================================================================
# Organizations (update + delete only; list/get/create already existed)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityOrganizations:
    async def test_organization_update_and_delete(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.organizations import (
            delete_organization,
            update_organization,
        )

        # Create an org via REST (create_organization is the existing ORM tool;
        # the parity surface only adds update + delete).
        name = f"mcp-parity-org-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/organizations",
            headers=platform_admin.headers,
            json={"name": name, "domain": f"{uuid4().hex[:8]}.mcp-parity.test"},
        )
        assert create_resp.status_code == 201
        org_id = create_resp.json()["id"]

        renamed = f"mcp-parity-org-renamed-{uuid4().hex[:8]}"
        update_result = await update_organization(
            admin_context, organization_ref=org_id, name=renamed
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("name") == renamed

        delete_result = await delete_organization(
            admin_context, organization_ref=org_id
        )
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == org_id


# =============================================================================
# Integrations
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityIntegrations:
    async def test_get_integration_by_uuid(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        """``get_integration`` thin-wrapper round-trips a created integration."""
        from src.services.mcp_server.tools.integrations import get_integration

        name = f"mcp-parity-get-int-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            result = await get_integration(
                admin_context, integration_ref=integration_id
            )
            payload = result.structured_content or {}
            assert "error" not in payload, payload
            assert str(payload.get("id")) == str(integration_id)
            assert payload.get("name") == name
            # Detail payload includes mappings + config_schema keys.
            assert "mappings" in payload
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )

    async def test_integration_and_mapping_roundtrip(
        self, admin_context, e2e_client, platform_admin, org1
    ) -> None:
        from src.services.mcp_server.tools.integrations import (
            add_integration_mapping,
            create_integration,
            update_integration,
            update_integration_mapping,
        )

        # create integration
        name = f"mcp-parity-int-{uuid4().hex[:8]}"
        create_result = await create_integration(
            admin_context,
            name=name,
            entity_id_name="Tenant",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        integration_id = str(created["id"])

        # update integration (rename)
        renamed = f"mcp-parity-int-renamed-{uuid4().hex[:8]}"
        update_result = await update_integration(
            admin_context, integration_ref=integration_id, name=renamed
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated

        # add mapping (by org name ref)
        add_result = await add_integration_mapping(
            admin_context,
            integration_ref=renamed,
            organization=org1["name"],
            entity_id=f"tenant-{uuid4().hex[:8]}",
            entity_name="E2E Tenant",
        )
        mapping = add_result.structured_content or {}
        assert "error" not in mapping, mapping
        mapping_id = str(mapping["id"])

        # update mapping
        update_m_result = await update_integration_mapping(
            admin_context,
            integration_ref=renamed,
            mapping_id=mapping_id,
            entity_name="E2E Tenant (renamed)",
        )
        assert update_m_result.structured_content is not None
        assert "error" not in update_m_result.structured_content

        # Cleanup via REST.
        e2e_client.delete(
            f"/api/integrations/{integration_id}/mappings/{mapping_id}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            f"/api/integrations/{integration_id}",
            headers=platform_admin.headers,
        )


# =============================================================================
# Workflow lifecycle
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityWorkflow:
    async def test_workflow_update_grant_revoke(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.workflow import (
            grant_workflow_role,
            revoke_workflow_role,
            update_workflow,
        )

        # Create a workflow via the register endpoint so delete_workflow has
        # something to operate on; our parity tool for update/delete does not
        # create workflows.
        path = f"apps/mcp_parity/wf_{uuid4().hex[:6]}.py"
        content = (
            "from bifrost import workflow\n"
            "\n"
            "@workflow(description='test workflow')\n"
            "def do_thing(x: str = '') -> str:\n"
            "    return x\n"
        )
        write_resp = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={"path": path, "content": content, "encoding": "utf-8"},
        )
        assert write_resp.status_code in (200, 201)
        register_resp = e2e_client.post(
            "/api/workflows/register",
            headers=platform_admin.headers,
            json={"path": path, "function_name": "do_thing"},
        )
        assert register_resp.status_code in (200, 201), register_resp.text
        workflow_id = register_resp.json()["id"]
        UUID(workflow_id)

        # update: change description
        update_result = await update_workflow(
            admin_context,
            workflow_ref=workflow_id,
            description="updated via MCP parity",
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated

        # Create a role via REST to grant access.
        role_name = f"mcp-parity-wfrole-{uuid4().hex[:8]}"
        role_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": role_name, "description": "test", "permissions": {}},
        )
        assert role_resp.status_code == 201
        role_id = role_resp.json()["id"]

        try:
            grant_result = await grant_workflow_role(
                admin_context, workflow_ref=workflow_id, role_ref=role_name
            )
            assert grant_result.structured_content is not None
            assert "error" not in grant_result.structured_content

            revoke_result = await revoke_workflow_role(
                admin_context, workflow_ref=workflow_id, role_ref=role_name
            )
            assert revoke_result.structured_content is not None
            assert "error" not in revoke_result.structured_content
        finally:
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)

    async def test_workflow_delete_with_force(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.workflow import delete_workflow

        # Register a fresh workflow, then delete it via the parity tool.
        # We pass force_deactivation=True to short-circuit any history check.
        path = f"apps/mcp_parity/del_{uuid4().hex[:6]}.py"
        content = (
            "from bifrost import workflow\n"
            "\n"
            "@workflow(description='delete target')\n"
            "def to_delete(x: str = '') -> str:\n"
            "    return x\n"
        )
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={"path": path, "content": content, "encoding": "utf-8"},
        )
        register_resp = e2e_client.post(
            "/api/workflows/register",
            headers=platform_admin.headers,
            json={"path": path, "function_name": "to_delete"},
        )
        assert register_resp.status_code in (200, 201), register_resp.text
        workflow_id = register_resp.json()["id"]

        delete_result = await delete_workflow(
            admin_context,
            workflow_ref=workflow_id,
            force_deactivation=True,
        )
        # The delete endpoint returns either a plain dict (deleted OK) or a
        # 409 we surface as error. Happy path: no "error" in structured.
        assert delete_result.structured_content is not None
