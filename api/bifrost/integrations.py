"""
Integrations SDK for Bifrost.

Provides Python API for integration management and OAuth configuration.

All methods are async and must be awaited.
"""

from __future__ import annotations

from .client import get_client, raise_for_status_with_detail
from .models import IntegrationData, IntegrationMappingResponse
from ._context import resolve_scope, _execution_context
from ._local_transport import get as _get_local_transport


def _current_context():
    """Return the active ExecutionContext, or None if not in a workflow execution."""
    return _execution_context.get()


def _parse_integration_data(result: dict) -> IntegrationData:
    """Validate one integration payload and register its secrets.

    Shared by the HTTP and engine-local paths so decrypted OAuth tokens
    and secret config values are scrubbed from outputs identically.
    """
    from ._context import register_secret

    data = IntegrationData.model_validate(result)
    if data.oauth is not None:
        for secret_field in (data.oauth.access_token, data.oauth.refresh_token, data.oauth.client_secret):
            if secret_field:
                register_secret(secret_field)
    if data.config_secret_keys:
        for key in data.config_secret_keys:
            val = data.config.get(key)
            if val:
                register_secret(str(val))
    return data


class integrations:
    """
    Integration management operations.

    Allows workflows to retrieve integration configurations and mappings.

    All methods are async - await is required.
    """

    @staticmethod
    async def get(
        name: str, scope: str | None = None, oauth_scope: str | None = None
    ) -> IntegrationData | None:
        """
        Get integration configuration for an organization.

        Inside an engine child this resolves through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint.

        Returns the integration data including entity ID, configuration,
        and full OAuth details with decrypted credentials.

        When an org-specific mapping exists, returns that mapping's data.
        When no mapping exists, falls back to integration-level defaults.

        Args:
            name: Integration name
            scope: Organization scope override. Omit to use the execution
                context org (with automatic global fallback via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope (integration defaults).
            oauth_scope: Override OAuth scope for token request. When provided,
                triggers a fresh token fetch for client_credentials flows.
                Useful for accessing different resources with the same credentials.
                Example: "https://outlook.office365.com/.default" for Exchange API.

        Returns:
            IntegrationData | None: Integration data with attributes:
                - integration_id: str - UUID of the integration
                - entity_id: str | None - External entity ID (from mapping or default_entity_id)
                - entity_name: str | None - Display name for the mapped entity
                - config: dict[str, Any] - Configuration (org overrides + defaults)
                - oauth: OAuthCredentials | None - OAuth data with attributes:
                    - connection_name: str
                    - client_id: str
                    - client_secret: str | None (decrypted)
                    - authorization_url: str | None
                    - token_url: str | None (resolved with entity_id)
                    - scopes: list[str]
                    - access_token: str | None (decrypted)
                    - refresh_token: str | None (decrypted)
                    - expires_at: str | None (ISO format)
            Returns None if integration not found.

        Example:
            >>> from bifrost import integrations
            >>> integration = await integrations.get("HaloPSA")
            >>> if integration:
            ...     tenant_id = integration.entity_id
            ...     if integration.oauth:
            ...         client_id = integration.oauth.client_id
            ...         refresh_token = integration.oauth.refresh_token
            >>> # Get integration for specific org (provider orgs only)
            >>> org_int = await integrations.get("HaloPSA", scope="org-uuid-here")
            >>> # Get Exchange token (different scope than default Graph)
            >>> exchange = await integrations.get(
            ...     "Microsoft", scope="org-uuid",
            ...     oauth_scope="https://outlook.office365.com/.default"
            ... )
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent resolves this through the shared
            # integrations service over the dedicated channel — the same
            # service the HTTP endpoint calls. The Solution install id is
            # NOT sent: the parent derives it from its own dispatch context,
            # so a child can never forge another install's declared
            # connection. A local attempt never falls back to HTTP.
            result = await transport.call_integrations_get(
                name, effective_scope, oauth_scope
            )
            if result is None:
                return None
            return _parse_integration_data(result)
        client = get_client()
        request_data = {"name": name, "scope": effective_scope}
        if oauth_scope:
            request_data["oauth_scope"] = oauth_scope
        # Carry the solution install id (F2 pattern, mirrors tables.py): when this
        # execution belongs to a solution that DECLARED this integration, the server
        # escalates a missing integration to a 424 instead of a silent None.
        ctx = _current_context()
        solution_id = getattr(ctx, "solution_id", None) if ctx is not None else None
        if solution_id:
            request_data["solution"] = str(solution_id)
        response = await client.post(
            "/api/sdk/integrations/get",
            json=request_data
        )

        if response.status_code == 200:
            result = response.json()
            if result is None:
                return None
            return _parse_integration_data(result)
        raise_for_status_with_detail(response)
        raise AssertionError("unreachable")

    @staticmethod
    async def list_mappings(
        name: str,
        scope: str | None = None,
    ) -> list[IntegrationMappingResponse] | None:
        """
        List mappings for an integration in the resolved scope.

        Inside an engine child this lists through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint.

        Under the workflow execution model the API authenticates the engine
        sentinel (``is_superuser=True``), so the API-side C2 gate never
        fires on its own — the SDK-side ``resolve_scope`` is the security
        boundary for workflow callers. This method resolves ``scope``
        locally before posting; non-bypass workflow callers asking for a
        scope other than their own raise ``PermissionError`` here, not
        downstream.

        Args:
            name: Integration name
            scope: Organization scope to list. Defaults to the execution
                context's default scope. When that resolves to a provider org,
                all mappings are returned. Pass ``"global"`` (only valid for
                bypass callers — platform admin or provider-org member) to
                list across all orgs explicitly.

        Returns:
            list[IntegrationMappingResponse] | None: List of mappings.
            Returns None if integration not found.

        Example:
            >>> from bifrost import integrations
            >>> mappings = await integrations.list_mappings("Microsoft Partner")
            >>> if mappings:
            ...     for mapping in mappings:
            ...         org_id = mapping.organization_id
            ...         tenant_id = mapping.entity_id
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: never falls back to HTTP.
            json_result = await transport.call_integrations_list_mappings(
                name, effective_scope
            )
            if json_result is None:
                return None
            items = json_result.get("items", [])
            return [IntegrationMappingResponse.model_validate(item) for item in items]
        client = get_client()
        response = await client.post(
            "/api/sdk/integrations/list_mappings",
            json={"name": name, "scope": effective_scope},
        )

        if response.status_code == 200:
            json_result = response.json()
            if json_result is None:
                return None
            # API returns {"items": [...]} structure
            items = json_result.get("items", [])
            # Convert to IntegrationMappingResponse models
            return [IntegrationMappingResponse.model_validate(item) for item in items]
        raise_for_status_with_detail(response)
        raise AssertionError("unreachable")

    @staticmethod
    async def get_mapping(
        name: str,
        scope: str | None = None,
        entity_id: str | None = None,
    ) -> IntegrationMappingResponse | None:
        """
        Get a specific mapping by organization scope or entity ID.

        Inside an engine child this resolves through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint.

        Args:
            name: Integration name
            scope: Organization scope - can be:
                - None: Use execution context default org
                - org UUID string: Target specific organization
            entity_id: External entity ID (to look up mapping by entity)

        Returns:
            IntegrationMappingResponse | None: Mapping data with attributes:
                - id: str - Mapping UUID
                - integration_id: str - Associated integration ID
                - organization_id: str - Organization ID
                - entity_id: str - External entity ID
                - entity_name: str | None - Display name
                - oauth_token_id: str | None - Per-org OAuth token override ID
                - config: dict - Organization-specific config
                - created_at: datetime - Creation timestamp
                - updated_at: datetime - Last update timestamp
            Returns None if mapping not found.

        Example:
            >>> from bifrost import integrations
            >>> # Get by scope (org_id)
            >>> mapping = await integrations.get_mapping("HaloPSA", scope="org-123")
            >>> # Get by entity_id
            >>> mapping = await integrations.get_mapping("HaloPSA", entity_id="tenant-456")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: never falls back to HTTP.
            result = await transport.call_integrations_get_mapping(
                name, effective_scope, entity_id
            )
            if result is None:
                return None
            return IntegrationMappingResponse.model_validate(result)
        client = get_client()
        response = await client.post(
            "/api/sdk/integrations/get_mapping",
            json={"name": name, "scope": effective_scope, "entity_id": entity_id}
        )

        if response.status_code == 200:
            result = response.json()
            if result is None:
                return None
            return IntegrationMappingResponse.model_validate(result)
        raise_for_status_with_detail(response)
        raise AssertionError("unreachable")

    @staticmethod
    async def upsert_mapping(
        name: str,
        scope: str,
        entity_id: str,
        entity_name: str | None = None,
        config: dict | None = None,
    ) -> IntegrationMappingResponse:
        """
        Create or update a mapping for an organization.

        If a mapping already exists for the org, updates it.
        Otherwise creates a new mapping.

        Args:
            name: Integration name
            scope: Organization ID (required - the org to create mapping for)
            entity_id: External entity ID (e.g., tenant ID)
            entity_name: Optional display name for the entity
            config: Optional org-specific configuration overrides

        Returns:
            IntegrationMappingResponse: Created or updated mapping

        Raises:
            RuntimeError: If integration not found or operation fails

        Example:
            >>> from bifrost import integrations
            >>> mapping = await integrations.upsert_mapping(
            ...     "HaloPSA",
            ...     scope="org-123",
            ...     entity_id="tenant-456",
            ...     entity_name="Customer A",
            ...     config={"api_url": "https://customer-a.halopsa.com"}
            ... )
        """
        client = get_client()
        # Resolve SDK-side: the workflow engine authenticates the API as
        # the sentinel superuser, so the API-side C2 gate ALWAYS passes.
        # The SDK-side ``resolve_scope`` is what enforces "this workflow's
        # actual caller is allowed to mutate that org's mapping."
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/sdk/integrations/upsert_mapping",
            json={
                "name": name,
                "scope": effective_scope,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "config": config,
            }
        )

        if response.status_code == 200:
            return IntegrationMappingResponse.model_validate(response.json())
        else:
            error_detail = response.text
            raise RuntimeError(f"Failed to upsert mapping: {response.status_code} - {error_detail}")

    @staticmethod
    async def delete_mapping(name: str, scope: str) -> bool:
        """
        Delete a mapping for an organization.

        Args:
            name: Integration name
            scope: Organization ID (the org whose mapping to delete)

        Returns:
            bool: True if deleted, False if mapping was not found

        Example:
            >>> from bifrost import integrations
            >>> deleted = await integrations.delete_mapping("HaloPSA", scope="org-123")
        """
        client = get_client()
        # See upsert_mapping above for why the SDK-side resolve is the
        # real security boundary under engine-sentinel auth.
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/sdk/integrations/delete_mapping",
            json={"name": name, "scope": effective_scope},
        )

        if response.status_code == 200:
            return response.json().get("deleted", False)
        raise_for_status_with_detail(response)
        raise AssertionError("unreachable")
