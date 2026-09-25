"""
bifrost/users.py - User management SDK

Provides Python API for user operations from workflows.
Engine children use the parent-local users service; external callers use HTTP.
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
        from ._local_transport import get as _get_local_transport

        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent lists through the shared
            # users service over the dedicated channel (route defaults
            # for the filters the SDK does not expose, like the
            # unfiltered HTTP call). The ``org_id`` rides as the
            # ``scope`` filter, like the HTTP query string. A local
            # attempt never falls back to HTTP.
            envelope = await transport.call_users_list(
                org_id, include_inactive
            )
            return [
                UserPublic.model_validate(user)
                for user in envelope["items"]
            ]
        client = get_client()
        params: dict[str, str] = {}
        if org_id:
            params["scope"] = org_id
        if include_inactive:
            params["include_inactive"] = "true"

        response = await client.get("/api/users", params=params)
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
        from ._local_transport import get as _get_local_transport

        from .client import BifrostAPIError

        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent reads through the shared
            # users service over the dedicated channel. Error mapping
            # matches the HTTP path below; a local attempt never falls
            # back to HTTP.
            try:
                data = await transport.call_users_get(user_id)
            except BifrostAPIError as e:
                if e.response.status_code == 404:
                    return None
                raise
            return UserPublic.model_validate(data)
        client = get_client()
        response = await client.get(f"/api/users/{user_id}")
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
        from ._local_transport import get as _get_local_transport

        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent creates through the shared
            # users service over the dedicated channel. Error mapping
            # matches the HTTP path below; a local attempt never falls
            # back to HTTP.
            data = await transport.call_users_create(
                email, name, is_superuser, org_id, is_active
            )
            return UserPublic.model_validate(data)
        client = get_client()
        payload = {
            "email": email,
            "name": name,
            "is_superuser": is_superuser,
            "is_active": is_active,
        }
        if org_id:
            payload["organization_id"] = org_id

        response = await client.post("/api/users", json=payload)
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
        from ._local_transport import get as _get_local_transport

        from .client import BifrostAPIError

        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent updates through the shared
            # users service over the dedicated channel. Error mapping
            # matches the HTTP path below; a local attempt never falls
            # back to HTTP.
            try:
                data = await transport.call_users_update(
                    user_id, dict(updates)
                )
            except BifrostAPIError as e:
                if e.response.status_code == 404:
                    raise ValueError(f"User not found: {user_id}") from None
                raise
            return UserPublic.model_validate(data)
        client = get_client()
        response = await client.patch(f"/api/users/{user_id}", json=updates)
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
        from ._local_transport import get as _get_local_transport

        from .client import BifrostAPIError

        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent deletes through the shared
            # users service over the dedicated channel. Error mapping
            # matches the HTTP path below; a local attempt never falls
            # back to HTTP.
            try:
                await transport.call_users_delete(user_id)
            except BifrostAPIError as e:
                if e.response.status_code == 404:
                    raise ValueError(f"User not found: {user_id}") from None
                raise
            return True
        client = get_client()
        response = await client.delete(f"/api/users/{user_id}")
        if response.status_code == 404:
            raise ValueError(f"User not found: {user_id}")
        raise_for_status_with_detail(response)
        return True
