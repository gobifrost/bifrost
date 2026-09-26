"""
Organization management SDK for Bifrost.

Provides Python API for organization operations from workflows.

Inside an engine child these operations go through the worker's private Unix
socket (the parent serves the real HTTP endpoints); external callers use HTTP.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import get_client, raise_for_status_with_detail
from .models import Organization

logger = logging.getLogger(__name__)


class organizations:
    """
    Organization management operations.

    All methods are async - await is required.
    """

    @staticmethod
    async def create(name: str, domain: str | None = None, is_active: bool = True) -> Organization:
        """
        Create a new organization.

        Requires: Platform admin privileges

        Args:
            name: Organization name
            domain: Organization domain (optional)
            is_active: Whether the organization is active (default: True)

        Returns:
            Organization: Created organization object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If validation fails

        Example:
            >>> from bifrost import organizations
            >>> org = await organizations.create("Acme Corp", domain="acme.com")
        """
        client = get_client()
        response = await client.engine_request(
            "POST",
            "/api/organizations",
            json={
                "name": name,
                "domain": domain,
                "is_active": is_active,
            },
        )
        raise_for_status_with_detail(response)
        data = response.json()
        return Organization.model_validate(data)

    @staticmethod
    async def get(org_id: str) -> Organization:
        """
        Get organization by ID.

        Args:
            org_id: Organization ID

        Returns:
            Organization: Organization object

        Raises:
            ValueError: If organization not found

        Example:
            >>> from bifrost import organizations
            >>> org = await organizations.get("org-123")
            >>> print(org.name)
        """
        client = get_client()
        response = await client.engine_request(
            "GET", f"/api/organizations/{org_id}"
        )
        if response.status_code == 404:
            raise ValueError(f"Organization not found: {org_id}")
        raise_for_status_with_detail(response)
        data = response.json()
        return Organization.model_validate(data)

    @staticmethod
    async def list() -> list[Organization]:
        """
        List all organizations.

        Requires: Platform admin privileges

        Returns:
            list[Organization]: List of organization objects

        Raises:
            PermissionError: If user is not platform admin

        Example:
            >>> from bifrost import organizations
            >>> orgs = await organizations.list()
            >>> for org in orgs:
            ...     print(f"{org.name}: {org.domain}")
        """
        client = get_client()
        response = await client.engine_request("GET", "/api/organizations")
        raise_for_status_with_detail(response)
        data = response.json()
        return [Organization.model_validate(org) for org in data]

    @staticmethod
    async def update(org_id: str, **updates: Any) -> Organization:
        """
        Update an organization.

        Requires: Platform admin privileges

        Args:
            org_id: Organization ID
            **updates: Fields to update (name, domain, is_active)

        Returns:
            Organization: Updated organization object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If organization not found or validation fails

        Example:
            >>> from bifrost import organizations
            >>> org = await organizations.update("org-123", name="New Name")
        """
        client = get_client()

        # Build request body with only provided updates
        update_payload = {}
        if "name" in updates:
            update_payload["name"] = updates["name"]
        if "domain" in updates:
            update_payload["domain"] = updates["domain"]
        if "is_active" in updates:
            update_payload["is_active"] = updates["is_active"]

        response = await client.engine_request(
            "PATCH",
            f"/api/organizations/{org_id}",
            json=update_payload,
        )
        if response.status_code == 404:
            raise ValueError(f"Organization not found: {org_id}")
        raise_for_status_with_detail(response)
        data = response.json()
        return Organization.model_validate(data)

    @staticmethod
    async def delete(org_id: str) -> bool:
        """
        Delete an organization (soft delete - sets is_active to false).

        Requires: Platform admin privileges

        Args:
            org_id: Organization ID

        Returns:
            bool: True if organization was deleted

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If organization not found

        Example:
            >>> from bifrost import organizations
            >>> deleted = await organizations.delete("org-123")
        """
        client = get_client()
        response = await client.engine_request(
            "DELETE", f"/api/organizations/{org_id}"
        )
        if response.status_code == 404:
            raise ValueError(f"Organization not found: {org_id}")
        raise_for_status_with_detail(response)
        return True
