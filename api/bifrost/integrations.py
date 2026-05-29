"""
Integrations SDK for Bifrost.

Provides Python API for integration management and OAuth configuration.

All methods are async and must be awaited.
"""

from __future__ import annotations

import logging

from .client import get_client
from .models import IntegrationData, IntegrationMappingResponse
from ._context import resolve_scope

logger = logging.getLogger(__name__)


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
        client = get_client()
        effective_scope = resolve_scope(scope)
        request_data = {"name": name, "scope": effective_scope}
        if oauth_scope:
            request_data["oauth_scope"] = oauth_scope
        response = await client.post(
            "/api/sdk/integrations/get",
            json=request_data
        )

        if response.status_code == 200:
            result = response.json()
            if result is None:
                return None
            data = IntegrationData.model_validate(result)
            from ._context import register_secret
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
        else:
            logger.warning(f"Integrations API call failed: {response.status_code}")
            return None

    @staticmethod
    async def list_mappings(
        name: str,
        scope: str | None = None,
    ) -> list[IntegrationMappingResponse] | None:
        """
        List mappings for an integration in the resolved scope.

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
        client = get_client()
        effective_scope = resolve_scope(scope)
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
        else:
            logger.warning(f"Integrations API call failed: {response.status_code}")
            return None

    @staticmethod
    async def get_mapping(
        name: str,
        scope: str | None = None,
        entity_id: str | None = None,
    ) -> IntegrationMappingResponse | None:
        """
        Get a specific mapping by organization scope or entity ID.

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
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/sdk/integrations/get_mapping",
            json={"name": name, "scope": effective_scope, "entity_id": entity_id}
        )

        if response.status_code == 200:
            result = response.json()
            if result is None:
                return None
            return IntegrationMappingResponse.model_validate(result)
        else:
            logger.warning(f"Integrations get_mapping API call failed: {response.status_code}")
            return None

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
        else:
            logger.warning(f"Integrations delete_mapping API call failed: {response.status_code}")
            return False
