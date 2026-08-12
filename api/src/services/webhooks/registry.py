"""
Webhook adapter registry for the Bifrost event system.

Manages discovery and lookup of webhook adapters:
- Built-in adapters (generic, microsoft_graph)
- User-defined adapters from workspace/adapters/
"""

import logging
from typing import Any

from src.config import get_settings
from src.core.log_safety import log_safe
from src.services.webhooks.adapters import BUILTIN_ADAPTERS
from src.services.webhooks.protocol import WebhookAdapter

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """
    Registry for webhook adapters.

    Provides lookup and discovery of adapters by name.
    Caches adapter instances for reuse.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, type[WebhookAdapter]] = {}
        self._instances: dict[str, WebhookAdapter] = {}
        self._loaded_custom = False

        # Register built-in adapters
        for name, adapter_cls in BUILTIN_ADAPTERS.items():
            self.register(name, adapter_cls)

        if get_settings().environment != "production":
            from src.services.webhooks.adapters.local_fixture import (
                LocalFixtureWebhookAdapter,
            )

            self.register(LocalFixtureWebhookAdapter.name, LocalFixtureWebhookAdapter)

    def register(self, name: str, adapter_cls: type[WebhookAdapter]) -> None:
        """
        Register an adapter class.

        Args:
            name: Adapter name (used for lookup).
            adapter_cls: Adapter class to register.
        """
        self._adapters[name] = adapter_cls
        # Clear cached instance if re-registering
        self._instances.pop(name, None)
        logger.debug(f"Registered webhook adapter: {name}")

    def get(self, name: str | None) -> WebhookAdapter | None:
        """
        Get adapter instance by name.

        Args:
            name: Adapter name. None or empty returns generic adapter.

        Returns:
            Adapter instance, or None if not found.
        """
        # Default to generic adapter
        if not name:
            name = "generic"

        # Check cache first
        if name in self._instances:
            return self._instances[name]

        # Load custom adapters on first non-builtin lookup
        if name not in self._adapters and not self._loaded_custom:
            self._load_custom_adapters()

        # Get adapter class
        adapter_cls = self._adapters.get(name)
        if not adapter_cls:
            return None

        # Create and cache instance
        try:
            instance = adapter_cls()
            self._instances[name] = instance
            return instance
        except Exception as e:
            logger.error(f"Failed to instantiate adapter {log_safe(name)}: {log_safe(e)}")
            return None

    def get_all(self) -> list[WebhookAdapter]:
        """
        Get all registered adapters.

        Returns:
            List of adapter instances.
        """
        # Load custom adapters if not already loaded
        if not self._loaded_custom:
            self._load_custom_adapters()

        adapters = []
        for name in self._adapters:
            adapter = self.get(name)
            if adapter:
                adapters.append(adapter)
        return adapters

    def get_adapter_info(self, name: str | None) -> dict[str, Any] | None:
        """
        Get adapter metadata without instantiating.

        Args:
            name: Adapter name.

        Returns:
            Dict with adapter metadata, or None if not found.
        """
        adapter = self.get(name)
        if not adapter:
            return None

        return {
            "name": adapter.name,
            "display_name": adapter.display_name,
            "description": adapter.description,
            "requires_integration": adapter.requires_integration,
            "config_schema": adapter.config_schema,
            "supports_renewal": adapter.renewal_interval is not None,
        }

    def list_adapters(self) -> list[dict[str, Any]]:
        """
        List all available adapters with metadata.

        Returns:
            List of adapter metadata dicts.
        """
        adapters = []
        for adapter in self.get_all():
            adapters.append({
                "name": adapter.name,
                "display_name": adapter.display_name,
                "description": adapter.description,
                "requires_integration": adapter.requires_integration,
                "config_schema": adapter.config_schema,
                "supports_renewal": adapter.renewal_interval is not None,
            })
        return adapters

    def _load_custom_adapters(self) -> None:
        """
        Load custom adapters from database.

        Custom adapters would be discovered from workspace files stored
        in the database that use the @adapter decorator.
        """
        self._loaded_custom = True

        # TODO: Implement custom adapter discovery from database
        # This would:
        # 1. Query file_index for adapters/*.py files
        # 2. Load code from database and find classes with @adapter decorator
        # 3. Register them in the registry
        #
        # For now, only built-in adapters are supported.
        # Custom adapter support will be added in a future phase.

        logger.debug("Custom adapter discovery not yet implemented")


# Global registry instance
_registry: AdapterRegistry | None = None


def get_adapter_registry() -> AdapterRegistry:
    """
    Get the global adapter registry.

    Creates the registry on first access.
    """
    global _registry
    if _registry is None:
        _registry = AdapterRegistry()
    return _registry


def get_adapter(name: str | None) -> WebhookAdapter | None:
    """
    Convenience function to get an adapter by name.

    Args:
        name: Adapter name. None returns generic adapter.

    Returns:
        Adapter instance, or None if not found.
    """
    return get_adapter_registry().get(name)
