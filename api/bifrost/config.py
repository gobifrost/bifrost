"""
Configuration SDK for Bifrost - API-only implementation.

Provides Python API for configuration management (get, set, list, delete).
All operations go through HTTP API endpoints.
All methods are async and must be awaited.
"""

from __future__ import annotations

from typing import Any

from .client import (
    get_client,
    get_engine_socket_path,
    raise_for_status_with_detail,
)
from .models import ConfigData
from ._context import resolve_scope
from ._local_transport import get as _get_local_transport


class config:
    """
    Configuration management operations.

    Allows workflows to read and write configuration values scoped to organizations.
    All operations are performed via HTTP API endpoints.

    All methods are async - await is required.
    """

    @staticmethod
    async def get(
        key: str,
        default: Any = None,
        scope: str | None = None,
    ) -> Any:
        """
        Get configuration value with automatic secret decryption.

        Inside an engine child this sends the ordinary HTTP request over the
        worker's private Unix socket (the client's shared transport) when the
        engine injected one, otherwise it uses the older local channel, and
        elsewhere it calls the SDK API endpoint over the network. Every path
        reaches the same service.

        Args:
            key: Configuration key
            default: Default value if key not found (optional)
            scope: Organization scope override. Omit to use the execution
                context org (with automatic global fallback via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope.

        Returns:
            Any: Configuration value, or default if the key is not set

        Raises:
            RuntimeError: If not authenticated
            httpx.HTTPStatusError: On a real request failure (4xx/5xx). A
                missing key is NOT an error — the endpoint returns 200 with a
                null body and ``default`` is returned. Permission and server
                errors are surfaced rather than masked as ``default``.

        Example:
            >>> from bifrost import config
            >>> api_key = await config.get("api_key")
            >>> timeout = await config.get("timeout", default=30)
            >>> org_setting = await config.get("key", scope="org-uuid-here")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None and get_engine_socket_path() is None:
            # Older local channel, still used for the SDK methods not yet
            # migrated to the socket (config.set/list/delete, etc.) and for
            # engine children whose worker does not serve a socket. Checked
            # before the client is used, so the local path never needs
            # credentials and never falls back to HTTP — failures raise loudly.
            result = await transport.call_config_get(key, effective_scope)
        else:
            # Engine-local path: the shared BifrostClient sends the ordinary
            # HTTP request over the worker's private Unix socket when the
            # engine injected one, and over the network otherwise. Same path,
            # body, bearer token, timeout, and error handling as every other
            # SDK request; a local failure raises and never falls back to the
            # network API.
            client = get_client()
            response = await client.engine_request(
                "POST",
                "/api/sdk/config/get",
                json={"key": key, "scope": effective_scope},
            )
            # A missing key comes back as 200 with a null body; anything else
            # (permission denied, server error, transport) must surface, not
            # be silently collapsed into the caller's default.
            raise_for_status_with_detail(response)
            result = response.json()

        if result is None:
            return default
        value = result.get("value", default)
        if result.get("config_type") == "secret" and isinstance(value, str):
            from ._context import register_secret
            register_secret(value)
        return value

    @staticmethod
    async def set(
        key: str,
        value: Any,
        is_secret: bool = False,
        scope: str | None = None,
    ) -> None:
        """
        Set configuration value.

        Inside an engine child this stores through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint to store configuration
        (writes directly to database).

        Args:
            key: Configuration key
            value: Configuration value (must be JSON-serializable)
            is_secret: If True, encrypts the value before storage
            scope: Organization scope override. Omit to use the execution
                context org. Pass an org UUID to target a specific org
                (provider orgs only). Pass None explicitly for global scope.

        Raises:
            RuntimeError: If not authenticated
            ValueError: If value is not JSON-serializable

        Example:
            >>> from bifrost import config
            >>> await config.set("api_url", "https://api.example.com")
            >>> await config.set("api_key", "secret123", is_secret=True)
            >>> await config.set("org_setting", "value", scope="org-uuid-here")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: never falls back to HTTP — failures raise
            # loudly below (a write may already have committed, so a retry
            # over HTTP could double-apply).
            await transport.call_config_set(key, value, is_secret, effective_scope)
            return
        client = get_client()
        response = await client.post(
            "/api/sdk/config/set",
            json={
                "key": key,
                "value": value,
                "is_secret": is_secret,
                "scope": effective_scope,
            }
        )
        raise_for_status_with_detail(response)

    @staticmethod
    async def list(scope: str | None = None) -> ConfigData:
        """
        List configuration key-value pairs.

        Inside an engine child this lists through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint.

        Note: Secret values are redacted as "[SECRET]".

        Args:
            scope: Organization scope override. Omit to use the execution
                context org (with automatic global fallback via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope.

        Returns:
            ConfigData: Configuration data with dot-notation and dict-like access:
                >>> cfg = await config.list()
                >>> cfg.api_url        # Dot notation access
                >>> cfg["api_url"]     # Dict-like access
                >>> "api_url" in cfg   # Containment check
                >>> cfg.keys()         # Iterate keys

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import config
            >>> cfg = await config.list()
            >>> api_url = cfg.api_url
            >>> timeout = cfg.timeout or 30
            >>> org_cfg = await config.list(scope="org-uuid-here")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: never falls back to HTTP.
            result = await transport.call_config_list(effective_scope)
            return ConfigData.model_validate({"data": result})
        client = get_client()
        response = await client.post(
            "/api/sdk/config/list",
            json={"scope": effective_scope}
        )
        raise_for_status_with_detail(response)
        return ConfigData.model_validate({"data": response.json()})

    @staticmethod
    async def delete(key: str, scope: str | None = None) -> bool:
        """
        Delete configuration value.

        Inside an engine child this deletes through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint to delete configuration
        (deletes directly from database).

        Args:
            key: Configuration key
            scope: Organization scope override. Omit to use the execution
                context org (with automatic global fallback via cascade).
                Pass an org UUID to target a specific org (provider orgs only).
                Pass None explicitly for global scope.

        Returns:
            bool: True if deleted successfully

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import config
            >>> await config.delete("old_api_url")
            >>> await config.delete("old_api_url")
        """
        effective_scope = resolve_scope(scope)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: never falls back to HTTP — failures raise
            # loudly below.
            return await transport.call_config_delete(key, effective_scope)
        client = get_client()
        response = await client.post(
            "/api/sdk/config/delete",
            json={"key": key, "scope": effective_scope}
        )
        raise_for_status_with_detail(response)
        return response.json()
