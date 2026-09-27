"""
bifrost/users.py - User management SDK

Provides Python API for user operations from workflows.

Inside an engine child these operations go through the worker's private Unix
socket (the parent serves the real HTTP endpoints); external callers use HTTP.
"""

from __future__ import annotations

from typing import Any

from .client import get_client, raise_for_status_with_detail
from .models import UserPublic


class users:
    """
    User management operations.

    All methods are async and must be awaited.
    """

    @staticmethod
    async def list(
        org_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[UserPublic]:
        """
        List users.

        Admin users can list all users across all organizations.
        Non-admin users can only list users in their own organization.

        Args:
            org_id: Optional organization ID to filter by (admin only)
            include_inactive: Include inactive (disabled) users (default: False)

        Returns:
            list[UserPublic]: List of user objects

        Raises:
            PermissionError: If non-admin tries to list users from other orgs
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import users
            >>> all_users = await users.list()
            >>> org_users = await users.list(org_id="org-123")
            >>> all_including_disabled = await users.list(include_inactive=True)
        """
        client = get_client()
        params: dict[str, str] = {}
        if org_id:
            params["scope"] = org_id
        if include_inactive:
            params["include_inactive"] = "true"

        response = await client.engine_request("GET", "/api/users", params=params)
        raise_for_status_with_detail(response)
        data = response.json()
        return [UserPublic.model_validate(user) for user in data]

    @staticmethod
    async def get(user_id: str) -> UserPublic | None:
        """
        Get user by ID.

        Args:
            user_id: User ID (UUID or email)

        Returns:
            UserPublic | None: User object or None if not found

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import users
            >>> user = await users.get("user-123")
            >>> if user:
            ...     print(user.email)
        """
        client = get_client()
        response = await client.engine_request("GET", f"/api/users/{user_id}")
        if response.status_code == 404:
            return None
        raise_for_status_with_detail(response)
        return UserPublic.model_validate(response.json())

    @staticmethod
    async def create(
        email: str,
        name: str,
        is_superuser: bool = False,
        org_id: str | None = None,
        is_active: bool = True,
    ) -> UserPublic:
        """
        Create a new user.

        Requires: Platform admin privileges

        Args:
            email: User email address
            name: User display name
            is_superuser: Whether user is a platform admin (default: False)
            org_id: Organization ID (required for non-superusers)
            is_active: Whether the user is active (default: True)

        Returns:
            UserPublic: Created user object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If validation fails (e.g., org_id missing for non-superuser)
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import users
            >>> user = await users.create(
            ...     email="john@example.com",
            ...     name="John Doe",
            ...     org_id="org-123"
            ... )
        """
        client = get_client()
        payload = {
            "email": email,
            "name": name,
            "is_superuser": is_superuser,
            "is_active": is_active,
        }
        if org_id:
            payload["organization_id"] = org_id

        response = await client.engine_request("POST", "/api/users", json=payload)
        raise_for_status_with_detail(response)
        return UserPublic.model_validate(response.json())

    @staticmethod
    async def update(user_id: str, **updates: Any) -> UserPublic:
        """
        Update a user.

        Requires: Platform admin privileges

        Args:
            user_id: User ID (UUID or email)
            **updates: Fields to update (email, name, is_active, is_superuser, etc.)

        Returns:
            UserPublic: Updated user object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If user not found or validation fails
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import users
            >>> user = await users.update("user-123", name="New Name", is_active=False)
        """
        client = get_client()
        response = await client.engine_request(
            "PATCH", f"/api/users/{user_id}", json=updates
        )
        if response.status_code == 404:
            raise ValueError(f"User not found: {user_id}")
        raise_for_status_with_detail(response)
        return UserPublic.model_validate(response.json())

    @staticmethod
    async def delete(user_id: str) -> bool:
        """
        Permanently delete a user.

        Requires: Platform admin privileges

        Args:
            user_id: User ID (UUID or email)

        Returns:
            bool: True if user was deleted

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If user not found or trying to delete self
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import users
            >>> deleted = await users.delete("user-123")
        """
        client = get_client()
        response = await client.engine_request("DELETE", f"/api/users/{user_id}")
        if response.status_code == 404:
            raise ValueError(f"User not found: {user_id}")
        raise_for_status_with_detail(response)
        return True
