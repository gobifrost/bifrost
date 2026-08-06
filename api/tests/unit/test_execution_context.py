import dataclasses

from src.sdk.context import ExecutionContext, Organization, ROIContext


class TestToPublicDict:

    def test_includes_all_public_fields(self):
        ctx = ExecutionContext(
            user_id="u-123",
            email="jack@test.com",
            name="Jack",
            scope="org-456",
            organization=Organization(id="org-456", name="Acme Corp", is_active=True, is_provider=False),
            is_platform_admin=True,
            is_function_key=False,
            execution_id="exec-789",
            workflow_name="my_workflow",
            is_agent=False,
            public_url="https://bifrost.example.com",
            parameters={"ticket_id": 42},
            startup={"preloaded": True},
            form_inputs={"ticket_id": "visitor"},
            embed={"ticket_id": "signed"},
            roi=ROIContext(time_saved=15, value=100.0),
        )
        result = ctx.to_public_dict()

        assert result["user_id"] == "u-123"
        assert result["email"] == "jack@test.com"
        assert result["name"] == "Jack"
        assert result["scope"] == "org-456"
        assert result["organization"] == {"id": "org-456", "name": "Acme Corp", "is_active": True, "is_provider": False}
        assert result["is_platform_admin"] is True
        assert result["is_function_key"] is False
        assert result["execution_id"] == "exec-789"
        assert result["workflow_name"] == "my_workflow"
        assert result["is_agent"] is False
        assert result["public_url"] == "https://bifrost.example.com"
        assert result["parameters"] == {"ticket_id": 42}
        assert result["startup"] == {"preloaded": True}
        assert result["form_inputs"] == {"ticket_id": "visitor"}
        assert result["embed"] == {"ticket_id": "signed"}
        assert result["roi"] == {"time_saved": 15, "value": 100.0}

    def test_excludes_private_fields(self):
        ctx = ExecutionContext(
            user_id="u-1", email="a@b.com", name="A",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="e-1",
        )
        result = ctx.to_public_dict()
        assert "_db" not in result
        assert "_integration_cache" not in result
        assert "_integration_calls" not in result
        assert "_dynamic_secrets" not in result
        assert "_scope_override" not in result

    def test_organization_none_for_global(self):
        ctx = ExecutionContext(
            user_id="u-1", email="a@b.com", name="A",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="e-1",
        )
        result = ctx.to_public_dict()
        assert result["organization"] is None
        assert result["scope"] == "GLOBAL"

    def test_startup_none_when_not_set(self):
        ctx = ExecutionContext(
            user_id="u-1", email="a@b.com", name="A",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="e-1",
        )
        result = ctx.to_public_dict()
        assert result["startup"] is None


class TestNoConfigField:
    """Regression guard: ExecutionContext must not carry eager config state.

    Config was removed so CLI and platform paths build identical contexts.
    If _config or get_config reappear, CLI/platform parity breaks.
    """

    def test_no_config_field(self):
        """ExecutionContext must not have a _config field."""
        field_names = {f.name for f in dataclasses.fields(ExecutionContext)}
        assert "_config" not in field_names
        assert "_config_resolver" not in field_names

    def test_no_get_config_method(self):
        """ExecutionContext must not have a get_config method."""
        assert not hasattr(ExecutionContext, "get_config")


class TestCLIPlatformParity:
    """Verify that CLI and platform execution paths build equivalent contexts.

    The CLI (_run_direct) builds ExecutionContext from /api/sdk/context response.
    The platform engine (execute()) builds ExecutionContext from ExecutionRequest.
    Both must produce contexts with the same identity, scope, and authorization.
    """

    def _build_cli_context(
        self,
        ctx_response: dict,
        workflow_name: str = "test_workflow",
    ) -> ExecutionContext:
        """Simulate what _run_direct does with a /api/sdk/context response.

        This mirrors api/bifrost/cli.py _run_direct lines 570-595.
        """
        import uuid

        user_info = ctx_response["user"]
        org_info = ctx_response.get("organization")

        org = Organization(
            id=org_info["id"],
            name=org_info.get("name", ""),
            is_active=org_info.get("is_active", True),
            is_provider=org_info.get("is_provider", False),
        ) if org_info else None
        scope = org_info["id"] if org_info else "GLOBAL"

        return ExecutionContext(
            user_id=user_info.get("id", "cli-user"),
            email=user_info.get("email", ""),
            name=user_info.get("name", "CLI User"),
            scope=scope,
            organization=org,
            is_platform_admin=user_info.get("is_superuser", False),
            is_function_key=False,
            execution_id=f"standalone-{uuid.uuid4()}",
            workflow_name=workflow_name,
        )

    def _build_engine_context(
        self,
        user_id: str,
        email: str,
        name: str,
        is_superuser: bool,
        org: Organization | None,
        workflow_name: str = "test_workflow",
    ) -> ExecutionContext:
        """Simulate what execute() does in engine.py lines 278-291."""
        return ExecutionContext(
            user_id=user_id,
            email=email,
            name=name,
            scope=org.id if org else "GLOBAL",
            organization=org,
            is_platform_admin=is_superuser,
            is_function_key=False,
            execution_id="engine-exec-123",
            workflow_name=workflow_name,
            public_url="http://localhost:8000",
        )

    def test_org_scoped_parity(self):
        """CLI and engine produce matching context for org-scoped execution."""
        # Simulated /api/sdk/context response with org_id override
        ctx_response = {
            "user": {
                "id": "user-abc",
                "email": "admin@test.com",
                "name": "Admin",
                "is_superuser": True,
            },
            "organization": {
                "id": "org-123",
                "name": "Acme Corp",
                "is_active": True,
                "is_provider": False,
            },
            "default_parameters": {},
            "track_executions": True,
        }

        org = Organization(
            id="org-123", name="Acme Corp", is_active=True, is_provider=False,
        )

        cli_ctx = self._build_cli_context(ctx_response)
        engine_ctx = self._build_engine_context(
            user_id="user-abc",
            email="admin@test.com",
            name="Admin",
            is_superuser=True,
            org=org,
        )

        # Identity
        assert cli_ctx.user_id == engine_ctx.user_id
        assert cli_ctx.email == engine_ctx.email
        assert cli_ctx.name == engine_ctx.name

        # Scope
        assert cli_ctx.scope == engine_ctx.scope == "org-123"
        assert cli_ctx.org_id == engine_ctx.org_id == "org-123"

        # Organization
        assert cli_ctx.organization is not None
        assert engine_ctx.organization is not None
        assert cli_ctx.organization.id == engine_ctx.organization.id
        assert cli_ctx.organization.name == engine_ctx.organization.name
        assert cli_ctx.organization.is_active == engine_ctx.organization.is_active
        assert cli_ctx.organization.is_provider == engine_ctx.organization.is_provider

        # Authorization
        assert cli_ctx.is_platform_admin == engine_ctx.is_platform_admin is True
        assert cli_ctx.is_function_key == engine_ctx.is_function_key is False

    def test_global_scope_parity(self):
        """CLI and engine produce matching context for GLOBAL scope."""
        ctx_response = {
            "user": {
                "id": "user-xyz",
                "email": "dev@test.com",
                "name": "Dev",
                "is_superuser": False,
            },
            "organization": None,
            "default_parameters": {},
            "track_executions": True,
        }

        cli_ctx = self._build_cli_context(ctx_response)
        engine_ctx = self._build_engine_context(
            user_id="user-xyz",
            email="dev@test.com",
            name="Dev",
            is_superuser=False,
            org=None,
        )

        assert cli_ctx.scope == engine_ctx.scope == "GLOBAL"
        assert cli_ctx.org_id is None
        assert engine_ctx.org_id is None
        assert cli_ctx.organization is None
        assert engine_ctx.organization is None
        assert cli_ctx.is_platform_admin == engine_ctx.is_platform_admin is False

    def test_provider_org_parity(self):
        """CLI and engine both propagate is_provider for scope override support."""
        ctx_response = {
            "user": {
                "id": "user-prov",
                "email": "provider@test.com",
                "name": "Provider Admin",
                "is_superuser": True,
            },
            "organization": {
                "id": "org-prov",
                "name": "Provider Corp",
                "is_active": True,
                "is_provider": True,
            },
            "default_parameters": {},
            "track_executions": True,
        }

        org = Organization(
            id="org-prov", name="Provider Corp", is_active=True, is_provider=True,
        )

        cli_ctx = self._build_cli_context(ctx_response)
        engine_ctx = self._build_engine_context(
            user_id="user-prov",
            email="provider@test.com",
            name="Provider Admin",
            is_superuser=True,
            org=org,
        )

        assert cli_ctx.organization is not None
        assert engine_ctx.organization is not None
        assert cli_ctx.organization.is_provider is True
        assert engine_ctx.organization.is_provider is True

        # Both should allow scope override via set_scope
        cli_ctx.set_scope("other-org")
        assert cli_ctx.org_id == "other-org"
        engine_ctx.set_scope("other-org")
        assert engine_ctx.org_id == "other-org"

    def test_context_proxy_works_with_cli_context(self):
        """The bifrost SDK context proxy works with CLI-built contexts."""
        from bifrost._context import (
            clear_execution_context,
            context,
            set_execution_context,
        )

        ctx_response = {
            "user": {
                "id": "user-cli",
                "email": "cli@test.com",
                "name": "CLI Dev",
                "is_superuser": False,
            },
            "organization": {
                "id": "org-cli",
                "name": "CLI Org",
                "is_active": True,
                "is_provider": False,
            },
            "default_parameters": {},
            "track_executions": True,
        }

        cli_ctx = self._build_cli_context(ctx_response, workflow_name="my_wf")
        set_execution_context(cli_ctx)

        try:
            # These are the properties workflows actually use
            assert context.user_id == "user-cli"
            assert context.email == "cli@test.com"
            assert context.org_id == "org-cli"
            assert context.scope == "org-cli"
            assert context.workflow_name == "my_wf"
            assert context.is_platform_admin is False
            org = context.organization
            assert org is not None
            assert org.name == "CLI Org"
        finally:
            clear_execution_context()
