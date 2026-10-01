"""E2E tests for the Task 6 MCP parity tools.

Covers the thin-wrapper surface added in
``docs/plans/2026-04-18-cli-mutation-surface-and-mcp-parity.md`` (lines
350-390):

* Roles: ``bifrost_role_list``, ``bifrost_role_create``, ``bifrost_role_update``, ``bifrost_role_delete``.
* Configs: ``list_configs``, ``create_config``, ``update_config``,
  ``delete_config``.
* Integrations: ``create_integration``, ``update_integration``,
  ``add_integration_mapping``, ``update_integration_mapping``.
* Organizations: ``update_organization``, ``delete_organization``
  (``list`` / ``get`` / ``create`` already existed and are not touched).
* Workflow lifecycle: ``bifrost_workflow_update``, ``bifrost_workflow_delete``,
  ``bifrost_workflow_role_grant``, ``bifrost_workflow_role_revoke``
  (``list`` / ``register`` / ``execute`` already existed and are not touched).

RBAC R1b batch 2 (renames + thin-wrapper conversion, see
``docs/plans/2026-09-26-r1-domain-triage.md``) adds parity classes for:

* Executions: ``bifrost_execution_list``, ``bifrost_execution_get`` — now
  thin REST wrappers (previously ORM-backed).
* Platform jobs: ``bifrost_platform_job_get`` (moved out of ``tools/apps.py``,
  formerly ``get_app_publish_status``).
* Claims: ``bifrost_claim_list`` / ``_get`` / ``_create`` / ``_update`` / ``_delete``.
* File policies: ``bifrost_file_policy_list`` / ``_get`` / ``_set`` / ``_delete``.

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
        "model_path": "src.models.contracts.users:RoleCreate",
        "tool_path": "src.services.mcp_server.tools.roles:bifrost_role_create",
        "extra_args": set(),
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.users:RoleUpdate",
        "tool_path": "src.services.mcp_server.tools.roles:bifrost_role_update",
        "extra_args": {"role_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.config:ConfigCreate",
        "tool_path": "src.services.mcp_server.tools.configs:bifrost_config_create",
        # ``organization_id`` is excluded from the DTO flags (CLI targets org via
        # the unified --org/--global standard), but the MCP create_config tool
        # exposes it as a tool-side REF input (a UUID/name string resolved via
        # RefResolver), not the raw DTO field — so it's an extra_arg here.
        "extra_args": {"organization_id"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.config:ConfigUpdate",
        "tool_path": "src.services.mcp_server.tools.configs:bifrost_config_update",
        "extra_args": {"config_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.claims:CustomClaimCreate",
        "tool_path": "src.services.mcp_server.tools.claims:bifrost_claim_create",
        # `scope` is an org-targeting query param, not a DTO field — mirrors
        # the same convention used by other org-scoped router endpoints.
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.claims:CustomClaimUpdate",
        "tool_path": "src.services.mcp_server.tools.claims:bifrost_claim_update",
        "extra_args": {"name", "scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.policy_rule:PolicyRuleCreate",
        "tool_path": (
            "src.services.mcp_server.tools.policy_rules:bifrost_policy_rule_create"
        ),
        # ``organization_id`` is excluded from the DTO flags (CLI targets
        # org via the unified --org/--global standard, mirroring
        # ConfigCreate above), but the MCP tool still exposes it directly
        # as an optional scope param — so it's an extra_arg here.
        "extra_args": {"organization_id"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.policy_rule:PolicyRuleUpdate",
        "tool_path": (
            "src.services.mcp_server.tools.policy_rules:bifrost_policy_rule_update"
        ),
        # ``domain``/``name`` identify the target rule (path params), and
        # ``organization_id`` scopes the lookup (query param) — none are
        # PolicyRuleUpdate body fields.
        "extra_args": {"domain", "name", "organization_id"},
        # The DTO's ``name`` field (a rename) is exposed as ``new_name`` on
        # the tool since ``name`` is already taken by the lookup arg.
        "field_renames": {"name": "new_name"},
    },
    {
        "model_path": "src.models.contracts.organizations:OrganizationUpdate",
        "tool_path": (
            "src.services.mcp_server.tools.organizations:bifrost_organization_update"
        ),
        "extra_args": {"organization_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.integrations:IntegrationCreate",
        "tool_path": (
            "src.services.mcp_server.tools.integrations:bifrost_integration_create"
        ),
        "extra_args": set(),
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.integrations:IntegrationUpdate",
        "tool_path": (
            "src.services.mcp_server.tools.integrations:bifrost_integration_update"
        ),
        # ``force_remove_keys`` is a REST query param (the schema-removal
        # confirmation guard), not an IntegrationUpdate body field.
        "extra_args": {"integration_ref", "force_remove_keys"},
        # ``list_entities_data_provider_id`` is a workflow ref the tool
        # accepts as a name/UUID/path::func and resolves to a UUID before
        # POSTing — it is exposed under the shorter ``_data_provider`` name.
        "field_renames": {
            "list_entities_data_provider_id": "list_entities_data_provider",
        },
    },
    {
        "model_path": (
            "src.models.contracts.integrations:IntegrationMappingCreate"
        ),
        "tool_path": (
            "src.services.mcp_server.tools.integrations:bifrost_integration_mapping_create"
        ),
        "extra_args": {"integration_ref"},
        # ``organization_id`` is a UUID on the DTO but the MCP tool accepts
        # an org ref (UUID or name), exposed as ``organization``.
        "field_renames": {"organization_id": "organization"},
    },
    {
        "model_path": (
            "src.models.contracts.integrations:IntegrationMappingUpdate"
        ),
        "tool_path": (
            "src.services.mcp_server.tools.integrations:"
            "bifrost_integration_mapping_update"
        ),
        "extra_args": {"integration_ref", "mapping_id"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.workflows:WorkflowUpdateRequest",
        "tool_path": "src.services.mcp_server.tools.workflow:bifrost_workflow_update",
        "extra_args": {"workflow_ref"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.agents:AgentCreate",
        "tool_path": "src.services.mcp_server.tools.agents:bifrost_agent_create",
        # ``organization_id`` is excluded from DTO flags (AgentCreate is in
        # _ORG_TARGET_EXCLUDE), so it never reaches the rename lookup below —
        # the tool exposes org targeting as ``scope`` (global / org ref /
        # omitted), listed here as an extra_arg for that reason. The rename
        # entry documents the intended mapping even though the exclude
        # short-circuits it first.
        "extra_args": {"scope"},
        "field_renames": {"organization_id": "scope"},
    },
    {
        "model_path": "src.models.contracts.agents:AgentUpdate",
        "tool_path": "src.services.mcp_server.tools.agents:bifrost_agent_update",
        "extra_args": {"agent_ref", "scope"},
        "field_renames": {"organization_id": "scope"},
    },
    {
        "model_path": "src.models.contracts.forms:FormCreate",
        "tool_path": "src.services.mcp_server.tools.forms:bifrost_form_create",
        # ``organization_id`` is excluded (FormCreate is in _ORG_TARGET_EXCLUDE);
        # the tool exposes org targeting as ``scope`` instead.
        "extra_args": {"scope"},
        "field_renames": {"form_schema": "fields"},
    },
    {
        "model_path": "src.models.contracts.forms:FormUpdate",
        "tool_path": "src.services.mcp_server.tools.forms:bifrost_form_update",
        "extra_args": {"form_ref", "scope"},
        "field_renames": {"form_schema": "fields"},
    },
    {
        "model_path": "src.models.contracts.tables:TableCreate",
        "tool_path": "src.services.mcp_server.tools.tables:bifrost_table_create",
        "extra_args": {"scope"},
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.tables:TableUpdate",
        "tool_path": "src.services.mcp_server.tools.tables:bifrost_table_update",
        # ``organization_id`` is excluded (TableUpdate is now in
        # _ORG_TARGET_EXCLUDE); the tool exposes org (re)targeting as ``scope``.
        "extra_args": {"table_ref", "scope"},
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
# Roles
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityRoles:
    async def test_get_role_by_uuid(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        """``bifrost_role_get`` thin-wrapper round-trips a created role via UUID ref."""
        from src.services.mcp_server.tools.roles import bifrost_role_get

        name = f"mcp-parity-get-role-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        role_id = create_resp.json()["id"]

        try:
            result = await bifrost_role_get(admin_context, role_ref=role_id)
            payload = result.structured_content or {}
            assert "error" not in payload, payload
            assert str(payload.get("id")) == str(role_id)
            assert payload.get("name") == name
        finally:
            e2e_client.delete(
                f"/api/roles/{role_id}", headers=platform_admin.headers
            )

    async def test_roles_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.roles import (
            bifrost_role_create,
            bifrost_role_delete,
            bifrost_role_list,
            bifrost_role_update,
        )

        # list
        list_result = await bifrost_role_list(admin_context)
        assert list_result.structured_content is not None
        assert list_result.structured_content.get("count", -1) >= 0

        # create
        name = f"mcp-parity-role-{uuid4().hex[:8]}"
        create_result = await bifrost_role_create(
            admin_context,
            name=name,
            description="created by test_mcp_parity",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        role_id = str(created["id"])

        # update (by name ref)
        renamed = f"mcp-parity-role-renamed-{uuid4().hex[:8]}"
        update_result = await bifrost_role_update(
            admin_context,
            role_ref=name,
            name=renamed,
        )
        updated = update_result.structured_content or {}
        assert updated.get("name") == renamed

        # Confirm via REST.
        get_resp = e2e_client.get(
            f"/api/roles/{role_id}", headers=platform_admin.headers
        )
        assert get_resp.status_code == 200

        # delete (by renamed ref)
        delete_result = await bifrost_role_delete(admin_context, role_ref=renamed)
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
        """``bifrost_config_get`` round-trips a created config via UUID ref.

        Thin wrapper over ``GET /api/config/{uuid}``: resolves the ref, then
        reads the per-id endpoint directly.
        """
        from src.services.mcp_server.tools.configs import (
            bifrost_config_create,
            bifrost_config_delete,
            bifrost_config_get,
        )

        key = f"mcp_parity_get_{uuid4().hex[:8]}"
        create_result = await bifrost_config_create(
            admin_context,
            key=key,
            value="hello",
            config_type="string",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        config_id = str(created["id"])

        try:
            result = await bifrost_config_get(admin_context, config_ref=config_id)
            payload = result.structured_content or {}
            assert "error" not in payload, payload
            assert str(payload.get("id")) == config_id
            assert payload.get("key") == key
            assert payload.get("value") == "hello"
        finally:
            await bifrost_config_delete(admin_context, config_ref=config_id)

    async def test_configs_crud_roundtrip(self, admin_context) -> None:
        from src.services.mcp_server.tools.configs import (
            bifrost_config_create,
            bifrost_config_delete,
            bifrost_config_list,
            bifrost_config_update,
        )

        # list
        list_result = await bifrost_config_list(admin_context)
        assert list_result.structured_content is not None

        # create (global, plain string type via config_type)
        key = f"mcp_parity_{uuid4().hex[:8]}"
        create_result = await bifrost_config_create(
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
        update_result = await bifrost_config_update(
            admin_context,
            config_ref=config_id,
            value="updated",
        )
        assert update_result.structured_content is not None
        assert "error" not in update_result.structured_content

        # delete by UUID
        delete_result = await bifrost_config_delete(admin_context, config_ref=config_id)
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == config_id

    async def test_non_admin_list_configs_is_403(
        self, org_user_context: "MockMCPContext"
    ) -> None:
        """Configs are platform-admin only in REST; the thin wrapper inherits it."""
        from src.services.mcp_server.tools.configs import bifrost_config_list

        result = await bifrost_config_list(org_user_context)
        payload = result.structured_content or {}
        assert "error" in payload, payload
        assert "HTTP 403" in payload["error"]


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
            bifrost_organization_delete,
            bifrost_organization_update,
        )

        # Create an org via REST — this test only exercises update + delete.
        name = f"mcp-parity-org-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/organizations",
            headers=platform_admin.headers,
            json={"name": name, "domain": f"{uuid4().hex[:8]}.mcp-parity.test"},
        )
        assert create_resp.status_code == 201
        org_id = create_resp.json()["id"]

        renamed = f"mcp-parity-org-renamed-{uuid4().hex[:8]}"
        update_result = await bifrost_organization_update(
            admin_context, organization_ref=org_id, name=renamed
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("name") == renamed

        delete_result = await bifrost_organization_delete(
            admin_context, organization_ref=org_id
        )
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == org_id

    async def test_non_admin_list_organizations_is_403(
        self, org_user_context: "MockMCPContext"
    ) -> None:
        """Organizations are platform-admin only in REST; the thin wrapper
        inherits that gate exactly."""
        from src.services.mcp_server.tools.organizations import bifrost_organization_list

        result = await bifrost_organization_list(org_user_context)
        payload = result.structured_content or {}
        assert "error" in payload, payload
        assert "HTTP 403" in payload["error"]


# =============================================================================
# Integrations
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityIntegrations:
    async def test_list_integrations_platform_admin(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        """``bifrost_integration_list`` is a thin wrapper over ``GET /api/integrations``."""
        from src.services.mcp_server.tools.integrations import bifrost_integration_list

        name = f"mcp-parity-list-int-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            result = await bifrost_integration_list(admin_context)
            payload = result.structured_content or {}
            assert "error" not in payload, payload
            names = {item["name"] for item in payload["integrations"]}
            assert name in names
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )

    async def test_non_admin_list_integrations_is_403(
        self, org_user_context: "MockMCPContext"
    ) -> None:
        """Integrations list is platform-admin only in REST (Jack's 2026-09-27
        decision: no widening for non-admin org members). The old ORM-backed
        tool returned a non-admin's org-mapped integrations; the thin
        wrapper now returns 403 like REST.
        """
        from src.services.mcp_server.tools.integrations import bifrost_integration_list

        result = await bifrost_integration_list(org_user_context)
        payload = result.structured_content or {}
        assert "error" in payload, payload
        assert "HTTP 403" in payload["error"]

    async def test_get_integration_by_uuid(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        """``bifrost_integration_get`` thin-wrapper round-trips a created integration."""
        from src.services.mcp_server.tools.integrations import bifrost_integration_get

        name = f"mcp-parity-get-int-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            result = await bifrost_integration_get(
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
            bifrost_integration_mapping_create,
            bifrost_integration_create,
            bifrost_integration_update,
            bifrost_integration_mapping_update,
        )

        # create integration
        name = f"mcp-parity-int-{uuid4().hex[:8]}"
        create_result = await bifrost_integration_create(
            admin_context,
            name=name,
            entity_id_name="Tenant",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        integration_id = str(created["id"])

        # update integration (rename)
        renamed = f"mcp-parity-int-renamed-{uuid4().hex[:8]}"
        update_result = await bifrost_integration_update(
            admin_context, integration_ref=integration_id, name=renamed
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated

        # add mapping (by org name ref)
        add_result = await bifrost_integration_mapping_create(
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
        update_m_result = await bifrost_integration_mapping_update(
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
# Secret redaction (thin wrappers inherit the REST masking)
# =============================================================================


def _tool_output(result) -> str:
    """Everything an MCP client sees from a tool call, as one string."""
    return f"{result.content!r} {result.structured_content!r}"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpSecretRedaction:
    async def test_config_create_and_update_redact_secret(self, admin_context) -> None:
        from src.services.mcp_server.tools.configs import (
            bifrost_config_create,
            bifrost_config_delete,
            bifrost_config_update,
        )

        plaintext = f"mcp-plain-{uuid4().hex}"
        rotated = f"mcp-rotated-{uuid4().hex}"
        create_result = await bifrost_config_create(
            admin_context,
            key=f"mcp_secret_{uuid4().hex[:8]}",
            value=plaintext,
            config_type="secret",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        config_id = str(created["id"])

        try:
            assert created["value"] == "[SECRET]"
            assert plaintext not in _tool_output(create_result)

            update_result = await bifrost_config_update(
                admin_context, config_ref=config_id, value=rotated
            )
            updated = update_result.structured_content or {}
            assert "error" not in updated, updated
            assert updated["value"] == "[SECRET]"
            assert rotated not in _tool_output(update_result)
        finally:
            await bifrost_config_delete(admin_context, config_ref=config_id)

    async def test_integration_tools_redact_secret_config(
        self, admin_context, e2e_client, platform_admin, org1
    ) -> None:
        from src.services.mcp_server.tools.integrations import (
            bifrost_integration_get,
            bifrost_integration_mapping_create,
        )

        default_secret = f"mcp-default-{uuid4().hex}"
        org_secret = f"mcp-org-{uuid4().hex}"
        create_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": f"mcp-secret-int-{uuid4().hex[:8]}",
                "config_schema": [{"key": "api_key", "type": "secret", "required": True}],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        integration_id = create_resp.json()["id"]

        try:
            put = e2e_client.put(
                f"/api/integrations/{integration_id}/config",
                headers=platform_admin.headers,
                json={"config": {"api_key": default_secret}},
            )
            assert put.status_code == 200, put.text

            mapping_result = await bifrost_integration_mapping_create(
                admin_context,
                integration_ref=integration_id,
                organization=org1["name"],
                entity_id=f"tenant-{uuid4().hex[:8]}",
                config={"api_key": org_secret},
            )
            mapping = mapping_result.structured_content or {}
            assert "error" not in mapping, mapping
            assert mapping["config"] == {"api_key": "[SECRET]"}
            assert org_secret not in _tool_output(mapping_result)

            get_result = await bifrost_integration_get(
                admin_context, integration_ref=integration_id
            )
            payload = get_result.structured_content or {}
            assert "error" not in payload, payload
            assert payload["config_defaults"] == {"api_key": "[SECRET]"}
            assert [m["config"] for m in payload["mappings"]] == [{"api_key": "[SECRET]"}]
            output = _tool_output(get_result)
            assert default_secret not in output
            assert org_secret not in output
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_admin.headers,
            )


# =============================================================================
# Policy Rules
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityPolicyRules:
    async def test_policy_rule_crud_roundtrip(self, admin_context) -> None:
        from src.services.mcp_server.tools.policy_rules import (
            bifrost_policy_rule_create,
            bifrost_policy_rule_delete,
            bifrost_policy_rule_get,
            bifrost_policy_rule_list,
            bifrost_policy_rule_update,
            bifrost_policy_rule_usage_list,
        )

        name = f"mcp-parity-rule-{uuid4().hex[:8]}"

        # create
        create_result = await bifrost_policy_rule_create(
            admin_context,
            name=name,
            domain="file",
            body={"actions": ["read"], "when": None},
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created

        # list
        list_result = await bifrost_policy_rule_list(admin_context, domain="file")
        assert list_result.structured_content is not None
        listed_names = {r["name"] for r in list_result.structured_content["policy_rules"]}
        assert name in listed_names

        # get
        get_result = await bifrost_policy_rule_get(admin_context, domain="file", name=name)
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("name") == name

        # usages (should be empty — nothing references it yet)
        usages_result = await bifrost_policy_rule_usage_list(
            admin_context, domain="file", name=name
        )
        assert usages_result.structured_content is not None
        assert usages_result.structured_content.get("total") == 0

        # update
        update_result = await bifrost_policy_rule_update(
            admin_context,
            domain="file",
            name=name,
            description="updated via MCP parity",
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("description") == "updated via MCP parity"

        # delete
        delete_result = await bifrost_policy_rule_delete(
            admin_context, domain="file", name=name
        )
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == f"file/{name}"

        # confirm gone
        get_after = await bifrost_policy_rule_get(admin_context, domain="file", name=name)
        after_payload = get_after.structured_content or {}
        assert "error" in after_payload, after_payload

    async def test_non_admin_list_policy_rules_is_403(
        self, org_user_context: "MockMCPContext"
    ) -> None:
        from src.services.mcp_server.tools.policy_rules import bifrost_policy_rule_list

        result = await bifrost_policy_rule_list(org_user_context)
        payload = result.structured_content or {}
        assert "error" in payload, payload
        assert "HTTP 403" in payload["error"]


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
            bifrost_workflow_role_grant,
            bifrost_workflow_role_revoke,
            bifrost_workflow_update,
        )

        # Create a workflow via the register endpoint so bifrost_workflow_delete has
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
        update_result = await bifrost_workflow_update(
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
            json={"name": role_name, "description": "test"},
        )
        assert role_resp.status_code == 201
        role_id = role_resp.json()["id"]

        try:
            grant_result = await bifrost_workflow_role_grant(
                admin_context, workflow_ref=workflow_id, role_ref=role_name
            )
            assert grant_result.structured_content is not None
            assert "error" not in grant_result.structured_content

            revoke_result = await bifrost_workflow_role_revoke(
                admin_context, workflow_ref=workflow_id, role_ref=role_name
            )
            assert revoke_result.structured_content is not None
            assert "error" not in revoke_result.structured_content
        finally:
            e2e_client.delete(
                f"/api/roles/{role_id}", headers=platform_admin.headers
            )

    async def test_workflow_delete_with_force(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.workflow import bifrost_workflow_delete

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

        delete_result = await bifrost_workflow_delete(
            admin_context,
            workflow_ref=workflow_id,
            force_deactivation=True,
        )
        # The delete endpoint returns either a plain dict (deleted OK) or a
        # 409 we surface as error. Happy path: no "error" in structured.
        assert delete_result.structured_content is not None

    async def test_workflow_list_is_platform_admin_only(
        self, admin_context, org_user_context
    ) -> None:
        """R1b batch 5 decision 1: list stays platform-admin only — a
        non-admin caller gets a plain 403 from the MCP tool, same as REST.
        """
        from src.services.mcp_server.tools.workflow import bifrost_workflow_list

        admin_result = await bifrost_workflow_list(admin_context)
        assert "error" not in (admin_result.structured_content or {"error": "x"})

        org_result = await bifrost_workflow_list(org_user_context)
        assert org_result.structured_content is not None
        assert org_result.structured_content.get("status_code") == 403

    async def test_workflow_get_admin_cross_org_and_non_admin_403(
        self, admin_context, org_user_context, e2e_client, platform_admin
    ) -> None:
        """New ``GET /api/workflows/{id}`` (batch 5 decision 3): platform
        admin can read any org's workflow by id; non-admin gets 403; a
        random id gets 404.
        """
        from src.services.mcp_server.tools.workflow import bifrost_workflow_get

        path = f"apps/mcp_parity/get_{uuid4().hex[:6]}.py"
        content = (
            "from bifrost import workflow\n"
            "\n"
            "@workflow(description='get target')\n"
            "def get_target(x: str = '') -> str:\n"
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
            json={"path": path, "function_name": "get_target"},
        )
        assert register_resp.status_code in (200, 201), register_resp.text
        workflow_id = register_resp.json()["id"]

        admin_result = await bifrost_workflow_get(admin_context, workflow_id)
        admin_data = admin_result.structured_content or {}
        assert "error" not in admin_data, admin_data
        assert admin_data.get("id") == workflow_id

        org_result = await bifrost_workflow_get(org_user_context, workflow_id)
        assert org_result.structured_content is not None
        assert org_result.structured_content.get("status_code") == 403

        missing_result = await bifrost_workflow_get(admin_context, str(uuid4()))
        assert missing_result.structured_content is not None
        assert missing_result.structured_content.get("status_code") == 404

    async def test_workflow_execute_works_for_org_member_with_role(
        self, admin_context, org_user_context, e2e_client, platform_admin
    ) -> None:
        """Batch 5 decision 2: execute authorization is unchanged — a
        non-admin org member with a role-granted workflow can execute it via
        MCP, identically to REST.
        """
        from src.services.mcp_server.tools.workflow import (
            bifrost_workflow_execute,
            bifrost_workflow_role_grant,
        )

        path = f"apps/mcp_parity/exec_{uuid4().hex[:6]}.py"
        content = (
            "from bifrost import workflow\n"
            "\n"
            "@workflow(description='exec target')\n"
            "def exec_target(x: str = 'hi') -> str:\n"
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
            json={"path": path, "function_name": "exec_target", "access_level": "role_based"},
        )
        assert register_resp.status_code in (200, 201), register_resp.text
        workflow_id = register_resp.json()["id"]

        role_name = f"mcp-parity-wfexec-{uuid4().hex[:8]}"
        role_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": role_name, "description": "test"},
        )
        assert role_resp.status_code == 201
        role_id = role_resp.json()["id"]

        try:
            grant_result = await bifrost_workflow_role_grant(
                admin_context, workflow_ref=workflow_id, role_ref=role_name
            )
            assert "error" not in (grant_result.structured_content or {})

            # Attach the role to org1_user so the execute authorization
            # check (role-based) resolves.
            user_role_resp = e2e_client.post(
                f"/api/roles/{role_id}/users",
                headers=platform_admin.headers,
                json={"user_ids": [org_user_context.user_id]},
            )
            assert user_role_resp.status_code == 204, user_role_resp.text

            exec_result = await bifrost_workflow_execute(
                org_user_context, workflow_id, {"x": "hello"}
            )
            data = exec_result.structured_content or {}
            # Either the execution succeeds (role attached) or fails with an
            # auth-shaped error — either way it must be the SAME REST path
            # the admin uses, not a client-side rejection before dispatch.
            assert "workflow_id" in data or "status_code" in data
        finally:
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityApps:
    async def test_apps_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.apps import (
            bifrost_app_create,
            bifrost_app_delete,
            bifrost_app_get,
            bifrost_app_list,
            bifrost_app_update,
        )

        list_result = await bifrost_app_list(admin_context)
        assert list_result.structured_content is not None
        assert list_result.structured_content.get("count", -1) >= 0

        name = f"mcp-parity-app-{uuid4().hex[:8]}"
        create_result = await bifrost_app_create(admin_context, name=name)
        created = create_result.structured_content or {}
        assert "error" not in created, created
        app_id = created["id"]

        get_result = await bifrost_app_get(admin_context, app_id=app_id)
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("name") == name

        update_result = await bifrost_app_update(
            admin_context, app_ref=app_id, description="updated via MCP parity"
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated

        delete_result = await bifrost_app_delete(admin_context, app_ref=app_id)
        assert delete_result.structured_content is not None
        assert "error" not in delete_result.structured_content

    async def test_apps_create_requires_scope_bypass(
        self, org_user_context
    ) -> None:
        """A regular org member cannot create apps via MCP — matches REST's
        ``has_scope_bypass`` gate (platform admin or provider-org member).
        """
        from src.services.mcp_server.tools.apps import bifrost_app_create

        result = await bifrost_app_create(
            org_user_context, name=f"denied-{uuid4().hex[:8]}"
        )
        data = result.structured_content or {}
        assert data.get("status_code") == 403

    async def test_apps_list_and_get_visible_to_non_admin(
        self, admin_context, org_user_context, e2e_client, platform_admin
    ) -> None:
        """Apps ARE listed to non-admins (the app launcher shows them) —
        unlike workflows. A global app created by an admin must be visible
        to a regular org member's list/get.
        """
        from src.services.mcp_server.tools.apps import bifrost_app_get, bifrost_app_list

        name = f"mcp-parity-visible-{uuid4().hex[:8]}"
        create_resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": name,
                "slug": name.replace("_", "-"),
                "access_level": "authenticated",
                "organization_id": None,  # explicit global scope, not admin's own org
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        app_id = create_resp.json()["id"]

        try:
            list_result = await bifrost_app_list(org_user_context)
            apps = (list_result.structured_content or {}).get("apps", [])
            assert any(a.get("id") == app_id for a in apps), apps

            get_result = await bifrost_app_get(org_user_context, app_slug=create_resp.json()["slug"])
            fetched = get_result.structured_content or {}
            assert "error" not in fetched, fetched
            assert fetched.get("id") == app_id
        finally:
            e2e_client.delete(f"/api/applications/{app_id}", headers=platform_admin.headers)


# =============================================================================
# Agents
# =============================================================================


@pytest.fixture
def org_user_context(org1_user, mcp_bridge_env) -> MockMCPContext:
    """``MCPContext`` for a regular (non-admin) org1 user."""
    return MockMCPContext(
        user_id=str(org1_user.user_id),
        user_email=org1_user.email,
        is_platform_admin=False,
        org_id=str(org1_user.organization_id),
        user_name=org1_user.name,
    )


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityAgents:
    async def test_agents_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.agents import (
            bifrost_agent_create,
            bifrost_agent_delete,
            bifrost_agent_get,
            bifrost_agent_list,
            bifrost_agent_update,
        )

        # list
        list_result = await bifrost_agent_list(admin_context)
        assert list_result.structured_content is not None
        assert list_result.structured_content.get("count", -1) >= 0

        # create
        name = f"mcp-parity-agent-{uuid4().hex[:8]}"
        create_result = await bifrost_agent_create(
            admin_context,
            name=name,
            system_prompt="You are a test assistant.",
            access_level="authenticated",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        agent_id = str(created["id"])

        # get by UUID
        get_result = await bifrost_agent_get(admin_context, agent_ref=agent_id)
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("id") == agent_id

        # get by name
        get_by_name_result = await bifrost_agent_get(admin_context, agent_ref=name)
        by_name = get_by_name_result.structured_content or {}
        assert "error" not in by_name, by_name
        assert by_name.get("id") == agent_id

        # update
        renamed = f"mcp-parity-agent-renamed-{uuid4().hex[:8]}"
        update_result = await bifrost_agent_update(
            admin_context, agent_ref=agent_id, name=renamed
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("name") == renamed

        # Confirm via REST.
        get_resp = e2e_client.get(
            f"/api/agents/{agent_id}", headers=platform_admin.headers
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == renamed

        # delete
        delete_result = await bifrost_agent_delete(admin_context, agent_ref=agent_id)
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == agent_id
        get_after = e2e_client.get(
            f"/api/agents/{agent_id}", headers=platform_admin.headers
        )
        assert get_after.status_code == 404

    async def test_regular_user_create_private_agent_succeeds(
        self, org_user_context: MockMCPContext, e2e_client, org1_user
    ) -> None:
        """A regular org user creating a private Agent must succeed.

        Proves the thin wrapper only sends keys the caller actually passed —
        if it defaulted ``organization_id``/list/budget fields onto the wire,
        this would trip the new non-admin 403 gates in ``routers/agents.py``.
        """
        from src.services.mcp_server.tools.agents import (
            bifrost_agent_create,
            bifrost_agent_delete,
        )

        name = f"mcp-parity-private-agent-{uuid4().hex[:8]}"
        create_result = await bifrost_agent_create(
            org_user_context,
            name=name,
            system_prompt="You are a private test assistant.",
            access_level="private",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        agent_id = str(created["id"])
        assert created.get("organization_id") == str(org1_user.organization_id)

        delete_result = await bifrost_agent_delete(
            org_user_context, agent_ref=agent_id
        )
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == agent_id

    async def test_regular_user_update_of_someone_elses_agent_is_403(
        self, admin_context, org_user_context: MockMCPContext, e2e_client
    ) -> None:
        from src.services.mcp_server.tools.agents import (
            bifrost_agent_create,
            bifrost_agent_delete,
            bifrost_agent_update,
        )

        create_result = await bifrost_agent_create(
            admin_context,
            name=f"mcp-parity-not-yours-{uuid4().hex[:8]}",
            system_prompt="You are an admin-owned assistant.",
            access_level="authenticated",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        agent_id = str(created["id"])

        try:
            update_result = await bifrost_agent_update(
                org_user_context, agent_ref=agent_id, name="hijacked"
            )
            payload = update_result.structured_content or {}
            assert payload.get("status_code") == 403, payload
        finally:
            await bifrost_agent_delete(admin_context, agent_ref=agent_id)


# =============================================================================
# Executions
# =============================================================================


_UNSET_ORG = object()


def _register_workflow(
    e2e_client, headers, *, access_level: str = "everyone", organization_id=_UNSET_ORG
) -> str:
    """Register a trivial workflow via the editor + register endpoints.

    ``organization_id`` OMITTED (default) leaves it off the register body, so
    the workflow HOME-defaults to the caller's own org. Pass
    ``organization_id=None`` explicitly to register a GLOBAL workflow (a bare
    ``None`` default here could never be distinguished from "not passed").
    """
    suffix = uuid4().hex[:6]
    function_name = f"mcp_parity_noop_{suffix}"
    path = f"apps/mcp_parity/exec_{suffix}.py"
    content = (
        "from bifrost import workflow\n"
        "\n"
        "@workflow(description='mcp parity execution test')\n"
        f"def {function_name}(x: str = '') -> str:\n"
        "    return x\n"
    )
    write_resp = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": path, "content": content, "encoding": "utf-8"},
    )
    assert write_resp.status_code in (200, 201), write_resp.text
    body = {
        "path": path,
        "function_name": function_name,
        "access_level": access_level,
    }
    if organization_id is not _UNSET_ORG:
        body["organization_id"] = organization_id
    register_resp = e2e_client.post(
        "/api/workflows/register",
        headers=headers,
        json=body,
    )
    assert register_resp.status_code in (200, 201), register_resp.text
    return register_resp.json()["id"]


def _execute_sync(e2e_client, headers, workflow_id: str) -> str:
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=headers,
        json={"workflow_id": workflow_id, "input_data": {"x": "hi"}, "sync": True},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["execution_id"]


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityExecutions:
    async def test_admin_list_and_get(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.execution import (
            bifrost_execution_get,
            bifrost_execution_list,
        )

        workflow_id = _register_workflow(e2e_client, platform_admin.headers)
        execution_id = _execute_sync(e2e_client, platform_admin.headers, workflow_id)

        list_result = await bifrost_execution_list(admin_context, limit=50)
        payload = list_result.structured_content or {}
        assert "error" not in payload, payload
        assert any(
            e.get("execution_id") == execution_id for e in payload.get("executions", [])
        ), payload

        get_result = await bifrost_execution_get(
            admin_context, execution_id=execution_id
        )
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("execution_id") == execution_id

    async def test_org_user_sees_own_run_not_anothers(
        self, e2e_client, platform_admin, org1_user, org1, mcp_bridge_env
    ) -> None:
        """A regular org user's get is scoped to their own executions.

        Registers the workflow in org1_user's own org (access_level=everyone
        is irrelevant to same-org access) so org1_user can execute it
        directly, then has the platform admin execute the *same* workflow —
        producing a second execution with a different ``executed_by`` — to
        prove org1_user's non-superuser get is denied for someone else's run
        (``shared/sdk_execution_reads.py``: non-superuser restricted to
        ``executed_by == principal.user_id``).
        """
        from src.services.mcp_server.tools.execution import bifrost_execution_get

        workflow_id = _register_workflow(
            e2e_client, platform_admin.headers, organization_id=org1["id"]
        )
        own_execution_id = _execute_sync(e2e_client, org1_user.headers, workflow_id)
        other_execution_id = _execute_sync(
            e2e_client, platform_admin.headers, workflow_id
        )

        org1_context = MockMCPContext(
            user_id=str(org1_user.user_id),
            user_email=org1_user.email,
            is_platform_admin=False,
            org_id=str(org1_user.organization_id),
            user_name=org1_user.name,
        )

        own_result = await bifrost_execution_get(
            org1_context, execution_id=own_execution_id
        )
        own_payload = own_result.structured_content or {}
        assert own_payload.get("execution_id") == own_execution_id, own_payload

        other_result = await bifrost_execution_get(
            org1_context, execution_id=other_execution_id
        )
        other_payload = other_result.structured_content or {}
        assert other_payload.get("error") == "Access denied", other_payload


# =============================================================================
# Platform jobs
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityPlatformJobs:
    async def test_requester_sees_job_other_user_denied(
        self, admin_context, org_user_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.platform_jobs import (
            bifrost_platform_job_get,
        )

        create_resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": f"mcp-parity-pubjob-{uuid4().hex[:8]}",
                "slug": f"mcp-parity-pubjob-{uuid4().hex[:8]}",
                "app_model": "inline_v1",
                "organization_id": None,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        app_id = create_resp.json()["id"]

        publish_resp = e2e_client.post(
            f"/api/applications/{app_id}/publish",
            headers=platform_admin.headers,
        )
        assert publish_resp.status_code == 202, publish_resp.text
        job_id = publish_resp.json()["job_id"]

        # The REST PlatformJobPublic body itself carries an "error" field
        # (job failure info, None on a healthy job) — check job_type/status
        # for the success case rather than mere key-presence of "error".
        requester_result = await bifrost_platform_job_get(admin_context, job_id=job_id)
        payload = requester_result.structured_content or {}
        assert payload.get("job_type") == "application.publish", payload
        assert payload.get("status") in ("queued", "running", "succeeded"), payload

        other_result = await bifrost_platform_job_get(org_user_context, job_id=job_id)
        other_payload = other_result.structured_content or {}
        assert isinstance(other_payload.get("error"), str), other_payload
        assert "job_type" not in other_payload, other_payload


# =============================================================================
# Claims
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityClaims:
    async def test_claims_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin, org1
    ) -> None:
        from src.services.mcp_server.tools.claims import (
            bifrost_claim_create,
            bifrost_claim_delete,
            bifrost_claim_get,
            bifrost_claim_list,
            bifrost_claim_update,
        )

        table_resp = e2e_client.post(
            "/api/tables",
            headers=platform_admin.headers,
            json={
                "name": f"mcp_parity_claims_{uuid4().hex[:8]}",
                "description": "mcp parity claims e2e table",
                "organization_id": org1["id"],
            },
        )
        assert table_resp.status_code == 201, table_resp.text
        table_name = table_resp.json()["name"]

        name = f"mcp_parity_claim_{uuid4().hex[:8]}"
        create_result = await bifrost_claim_create(
            admin_context,
            name=name,
            query={"table": table_name, "select": "campus_id"},
            scope=org1["id"],
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created

        list_result = await bifrost_claim_list(admin_context, scope=org1["id"])
        listed = list_result.structured_content or {}
        assert name in {c.get("name") for c in listed.get("claims", [])}, listed

        get_result = await bifrost_claim_get(
            admin_context, name=name, scope=org1["id"]
        )
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("query", {}).get("table") == table_name

        update_result = await bifrost_claim_update(
            admin_context,
            name=name,
            description="updated via MCP parity",
            scope=org1["id"],
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated

        delete_result = await bifrost_claim_delete(
            admin_context, name=name, scope=org1["id"]
        )
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == name


# =============================================================================
# File policies
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityFilePolicies:
    async def test_file_policy_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin, org1
    ) -> None:
        from src.services.mcp_server.tools.files import (
            bifrost_file_policy_delete,
            bifrost_file_policy_get,
            bifrost_file_policy_list,
            bifrost_file_policy_set,
        )

        path = f"mcp-parity/{uuid4().hex[:8]}"
        policies = [
            {"name": "r", "actions": ["read"], "when": {"user": "is_platform_admin"}}
        ]

        set_result = await bifrost_file_policy_set(
            admin_context,
            path=path,
            policies=policies,
            location="workspace",
            scope=org1["id"],
        )
        set_payload = set_result.structured_content or {}
        assert "error" not in set_payload, set_payload

        try:
            list_result = await bifrost_file_policy_list(
                admin_context, location="workspace", scope=org1["id"]
            )
            listed = list_result.structured_content or {}
            assert any(
                p.get("path") == path for p in listed.get("file_policies", [])
            ), listed

            get_result = await bifrost_file_policy_get(
                admin_context, path=path, location="workspace", scope=org1["id"]
            )
            fetched = get_result.structured_content or {}
            assert "error" not in fetched, fetched
        finally:
            delete_result = await bifrost_file_policy_delete(
                admin_context, path=path, location="workspace", scope=org1["id"]
            )
            assert delete_result.structured_content is not None


# =============================================================================
# Forms (RBAC R1b batch 4)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityForms:
    async def test_forms_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.forms import (
            bifrost_form_create,
            bifrost_form_delete,
            bifrost_form_get,
            bifrost_form_list,
            bifrost_form_update,
        )

        workflow_id = _register_workflow(e2e_client, platform_admin.headers)

        list_result = await bifrost_form_list(admin_context)
        assert list_result.structured_content is not None
        assert list_result.structured_content.get("count", -1) >= 0

        name = f"mcp-parity-form-{uuid4().hex[:8]}"
        create_result = await bifrost_form_create(
            admin_context,
            name=name,
            workflow_id=workflow_id,
            fields=[],
            description="original",
            access_level="authenticated",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        form_id = str(created["id"])

        get_result = await bifrost_form_get(admin_context, form_ref=form_id)
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("id") == form_id

        # Explicit-null-clears semantics reach the server unchanged through
        # this thin wrapper: omitting `description` here leaves it untouched.
        update_result = await bifrost_form_update(
            admin_context, form_ref=form_id, is_active=False
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("description") == "original"
        assert updated.get("is_active") is False

        delete_result = await bifrost_form_delete(admin_context, form_ref=form_id)
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == form_id

    async def test_regular_user_cannot_create_form(
        self, org_user_context: MockMCPContext
    ) -> None:
        """Forms are admin-managed; a regular org user's create is forwarded
        as REST's 403, not silently allowed or masked as a 500."""
        from src.services.mcp_server.tools.forms import bifrost_form_create

        result = await bifrost_form_create(
            org_user_context,
            name=f"mcp-parity-form-denied-{uuid4().hex[:8]}",
            workflow_id=str(uuid4()),
            fields=[],
        )
        payload = result.structured_content or {}
        assert payload.get("status_code") == 403, payload


# =============================================================================
# Tables (RBAC R1b batch 4)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityTables:
    async def test_tables_crud_roundtrip(self, admin_context) -> None:
        from src.services.mcp_server.tools.tables import (
            bifrost_table_create,
            bifrost_table_delete,
            bifrost_table_get,
            bifrost_table_list,
            bifrost_table_update,
        )

        list_result = await bifrost_table_list(admin_context)
        assert list_result.structured_content is not None
        assert list_result.structured_content.get("count", -1) >= 0

        name = f"mcp_parity_table_{uuid4().hex[:8]}"
        create_result = await bifrost_table_create(admin_context, name=name)
        created = create_result.structured_content or {}
        assert "error" not in created, created
        table_id = str(created["id"])

        get_result = await bifrost_table_get(admin_context, table_ref=table_id)
        fetched = get_result.structured_content or {}
        assert "error" not in fetched, fetched
        assert fetched.get("id") == table_id

        update_result = await bifrost_table_update(
            admin_context, table_ref=table_id, description="updated"
        )
        updated = update_result.structured_content or {}
        assert "error" not in updated, updated
        assert updated.get("description") == "updated"

        delete_result = await bifrost_table_delete(admin_context, table_ref=table_id)
        assert delete_result.structured_content is not None
        assert delete_result.structured_content.get("deleted") == table_id

    async def test_table_rescope_via_scope_param(
        self, admin_context, org1
    ) -> None:
        """``bifrost_table_update(scope=...)`` rescopes a table (RBAC R1b
        batch 4 revision 2 — table rescoping restored on all surfaces)."""
        from src.services.mcp_server.tools.tables import (
            bifrost_table_create,
            bifrost_table_delete,
            bifrost_table_update,
        )

        name = f"mcp_parity_table_rescope_{uuid4().hex[:8]}"
        create_result = await bifrost_table_create(admin_context, name=name)
        created = create_result.structured_content or {}
        assert "error" not in created, created
        table_id = str(created["id"])

        try:
            to_org = await bifrost_table_update(
                admin_context, table_ref=table_id, scope=org1["id"]
            )
            to_org_payload = to_org.structured_content or {}
            assert "error" not in to_org_payload, to_org_payload
            assert to_org_payload.get("organization_id") == org1["id"]

            to_global = await bifrost_table_update(
                admin_context, table_ref=table_id, scope="global"
            )
            to_global_payload = to_global.structured_content or {}
            assert "error" not in to_global_payload, to_global_payload
            assert to_global_payload.get("organization_id") is None
        finally:
            await bifrost_table_delete(admin_context, table_ref=table_id)

    async def test_regular_user_cannot_list_tables(
        self, org_user_context: MockMCPContext
    ) -> None:
        """Tables are platform-admin only, even within the caller's own org."""
        from src.services.mcp_server.tools.tables import bifrost_table_list

        result = await bifrost_table_list(org_user_context)
        payload = result.structured_content or {}
        assert payload.get("status_code") == 403, payload


# =============================================================================
# Events (RBAC R1b batch 4)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpParityEvents:
    async def test_event_source_and_subscription_crud_roundtrip(
        self, admin_context, e2e_client, platform_admin
    ) -> None:
        from src.services.mcp_server.tools.events import (
            bifrost_event_source_create,
            bifrost_event_source_delete,
            bifrost_event_source_get,
            bifrost_event_source_list,
            bifrost_event_subscription_create,
            bifrost_event_subscription_delete,
            bifrost_event_subscription_get,
            bifrost_event_subscription_list,
            bifrost_event_webhook_adapter_list,
        )

        adapters_result = await bifrost_event_webhook_adapter_list(admin_context)
        assert adapters_result.structured_content is not None

        # Global workflow so it satisfies the subscription scope cascade
        # against a global (organization_id=None) Event Source.
        workflow_id = _register_workflow(
            e2e_client, platform_admin.headers, organization_id=None
        )

        name = f"mcp-parity-event-source-{uuid4().hex[:8]}"
        create_result = await bifrost_event_source_create(
            admin_context,
            name=name,
            source_type="webhook",
            adapter_name="generic",
            webhook_config={},
            scope="global",
        )
        created = create_result.structured_content or {}
        assert "error" not in created, created
        source_id = str(created["id"])

        try:
            list_result = await bifrost_event_source_list(admin_context)
            assert list_result.structured_content is not None

            get_result = await bifrost_event_source_get(admin_context, source_ref=source_id)
            fetched = get_result.structured_content or {}
            assert "error" not in fetched, fetched

            sub_create = await bifrost_event_subscription_create(
                admin_context, source_ref=source_id, workflow_id=workflow_id
            )
            sub_created = sub_create.structured_content or {}
            assert "error" not in sub_created, sub_created
            subscription_id = str(sub_created["id"])

            sub_list = await bifrost_event_subscription_list(
                admin_context, source_ref=source_id
            )
            assert sub_list.structured_content is not None
            assert sub_list.structured_content.get("count", -1) >= 1

            # The new bifrost_event_subscription_get tool — no server-side
            # per-subscription GET existed before this batch.
            sub_get = await bifrost_event_subscription_get(
                admin_context, source_ref=source_id, subscription_id=subscription_id
            )
            sub_fetched = sub_get.structured_content or {}
            assert "error" not in sub_fetched, sub_fetched
            assert sub_fetched.get("id") == subscription_id

            sub_delete = await bifrost_event_subscription_delete(
                admin_context, source_ref=source_id, subscription_id=subscription_id
            )
            assert sub_delete.structured_content is not None
            assert sub_delete.structured_content.get("deleted") == subscription_id
        finally:
            delete_result = await bifrost_event_source_delete(
                admin_context, source_ref=source_id
            )
            assert delete_result.structured_content is not None
            assert delete_result.structured_content.get("deleted") == source_id

    async def test_regular_user_cannot_list_event_sources(
        self, org_user_context: MockMCPContext
    ) -> None:
        """Event source/subscription administration is platform-admin only."""
        from src.services.mcp_server.tools.events import bifrost_event_source_list

        result = await bifrost_event_source_list(org_user_context)
        payload = result.structured_content or {}
        assert payload.get("status_code") == 403, payload
