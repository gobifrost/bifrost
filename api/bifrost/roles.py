"""
bifrost/roles.py - Roles management SDK

Provides Python API for role operations (CRUD + user/form assignments).

Inside an engine child these operations go through the worker's private Unix
socket (the parent serves the real HTTP endpoints); external callers use HTTP.
"""

from __future__ import annotations

from typing import Any

from .client import get_client, raise_for_status_with_detail
from .models import Role


class roles:
    """
    Role management operations.

    Provides CRUD operations for roles and user/form assignments.

    All methods are async - await is required.
    """

    @staticmethod
    async def create(name: str, description: str = "") -> Role:
        """
        Create a new role.

        Requires: Platform admin or organization admin privileges

        Args:
            name: Role name
            description: Role description (optional)

        Returns:
            Role: Created role object

        Raises:
            PermissionError: If user lacks permission
            ValueError: If validation fails
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> role = await roles.create(
            ...     "Customer Manager",
            ...     description="Can manage customer data"
            ... )
        """
        client = get_client()
        response = await client.engine_request(
            "POST",
            "/api/roles",
            json={
                "name": name,
                "description": description,
                "is_active": True,
            }
        )
        raise_for_status_with_detail(response)
        return Role.model_validate(response.json())

    @staticmethod
    async def get(role_id: str) -> Role:
        """
        Get role by ID.

        Args:
            role_id: Role ID

        Returns:
            Role: Role object

        Raises:
            ValueError: If role not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> role = await roles.get("role-123")
            >>> print(role.name)
        """
        client = get_client()
        response = await client.engine_request("GET", f"/api/roles/{role_id}")
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)
        return Role.model_validate(response.json())

    @staticmethod
    async def list() -> list[Role]:
        """
        List all roles in the current organization.

        Returns:
            list[Role]: List of role objects

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> all_roles = await roles.list()
            >>> for role in all_roles:
            ...     print(f"{role.name}: {role.description}")
        """
        client = get_client()
        response = await client.engine_request("GET", "/api/roles")
        raise_for_status_with_detail(response)
        data = response.json()
        return [Role.model_validate(role) for role in data]

    @staticmethod
    async def update(role_id: str, **updates: Any) -> Role:
        """
        Update a role.

        Requires: Platform admin or organization admin privileges

        Args:
            role_id: Role ID
            **updates: Fields to update (name, description)

        Returns:
            Role: Updated role object

        Raises:
            PermissionError: If user lacks permission
            ValueError: If role not found or validation fails
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> role = await roles.update(
            ...     "role-123",
            ...     description="Updated description"
            ... )
        """
        client = get_client()
        response = await client.engine_request(
            "PATCH", f"/api/roles/{role_id}", json=updates
        )
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)
        return Role.model_validate(response.json())

    @staticmethod
    async def delete(role_id: str) -> None:
        """
        Delete a role. CASCADE removes all role assignments.

        Requires: Platform admin or organization admin privileges

        Args:
            role_id: Role ID

        Raises:
            PermissionError: If user lacks permission
            ValueError: If role not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> await roles.delete("role-123")
        """
        client = get_client()
        response = await client.engine_request("DELETE", f"/api/roles/{role_id}")
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)

    @staticmethod
    async def list_users(role_id: str) -> list[str]:
        """
        List all user IDs assigned to a role.

        Args:
            role_id: Role ID

        Returns:
            list[str]: List of user IDs

        Raises:
            ValueError: If role not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> user_ids = await roles.list_users("role-123")
            >>> for user_id in user_ids:
            ...     print(user_id)
        """
        client = get_client()
        response = await client.engine_request(
            "GET", f"/api/roles/{role_id}/users"
        )
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)
        data = response.json()
        return data.get("user_ids", [])

    @staticmethod
    async def list_forms(role_id: str) -> list[str]:
        """
        List all form IDs assigned to a role.

        Args:
            role_id: Role ID

        Returns:
            list[str]: List of form IDs

        Raises:
            ValueError: If role not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> form_ids = await roles.list_forms("role-123")
            >>> for form_id in form_ids:
            ...     print(form_id)
        """
        client = get_client()
        response = await client.engine_request(
            "GET", f"/api/roles/{role_id}/forms"
        )
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)
        data = response.json()
        return data.get("form_ids", [])

    @staticmethod
    async def assign_users(role_id: str, user_ids: list[str]) -> None:
        """
        Assign users to a role.

        Requires: Platform admin or organization admin privileges

        Args:
            role_id: Role ID
            user_ids: List of user IDs to assign

        Raises:
            PermissionError: If user lacks permission
            ValueError: If role or users not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> await roles.assign_users("role-123", ["user-1", "user-2"])
        """
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/roles/{role_id}/users",
            json={"user_ids": user_ids}
        )
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)

    @staticmethod
    async def assign_forms(role_id: str, form_ids: list[str]) -> None:
        """
        Assign forms to a role.

        Requires: Platform admin or organization admin privileges

        Args:
            role_id: Role ID
            form_ids: List of form IDs to assign

        Raises:
            PermissionError: If user lacks permission
            ValueError: If role or forms not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import roles
            >>> await roles.assign_forms("role-123", ["form-1", "form-2"])
        """
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/roles/{role_id}/forms",
            json={"form_ids": form_ids}
        )
        if response.status_code == 404:
            raise ValueError(f"Role not found: {role_id}")
        raise_for_status_with_detail(response)
