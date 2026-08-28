"""
Built-in webhook adapters for the Bifrost event system.
"""

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.adapters.microsoft_bot_framework import (
    MicrosoftBotFrameworkAdapter,
)
from src.services.webhooks.adapters.microsoft_graph import MicrosoftGraphAdapter

__all__ = [
    "GenericWebhookAdapter",
    "MicrosoftBotFrameworkAdapter",
    "MicrosoftGraphAdapter",
]

# Registry of built-in adapters
BUILTIN_ADAPTERS: dict[str, type] = {
    "generic": GenericWebhookAdapter,
    "microsoft_bot_framework": MicrosoftBotFrameworkAdapter,
    "microsoft_graph": MicrosoftGraphAdapter,
}
