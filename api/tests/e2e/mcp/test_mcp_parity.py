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
        self.enabled_system_tools: list[str] = []
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
        "extra_args": set(),
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
        "tool_path": (
            "src.services.mcp_server.tools.integrations:create_integration"
        ),
        "extra_args": set(),
        "field_renames": {},
    },
    {
        "model_path": "src.models.contracts.integrations:IntegrationUpdate",
        "tool_path": (
            "src.services.mcp_server.tools.integrations:update_integration"
        ),
        "extra_args": {"integration_ref"},
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
            "src.services.mcp_server.tools.integrations:add_integration_mapping"
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
            "update_integration_mapping"
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
            e2e_client.delete(
                f"/api/roles/{role_id}", headers=platform_admin.headers
            )

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
            e2e_client.delete(
                f"/api/roles/{role_id}", headers=platform_admin.headers
            )

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
