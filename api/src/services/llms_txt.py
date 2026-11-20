"""
LLMs.txt generator — reads the template and fills auto-generated tokens.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "llms.txt.md"

PYTHON_SDK_MODULES = (
    "agents", "ai", "artifacts", "config", "events", "executions", "files",
    "forms", "integrations", "knowledge", "organizations", "roles", "tables",
    "users", "workflows",
)


def generate_sdk_module_docs(module_name: str) -> str:
    """Generate one public SDK facade's reference for bounded model context."""
    if module_name not in PYTHON_SDK_MODULES:
        raise ValueError(f"Unknown Python SDK module: {module_name}")
    import bifrost

    from src.services.mcp_server.tools.sdk import _generate_module_docs

    return _generate_module_docs(module_name, getattr(bifrost, module_name))


def _generate_sdk_tokens() -> dict[str, str]:
    """Generate all SDK-related tokens from actual source code."""
    try:
        from src.services.mcp_server.tools.sdk import (
            _generate_decorator_docs,
            _generate_context_docs,
            _generate_error_docs,
            _generate_models_docs,
        )
        module_lines = [generate_sdk_module_docs(name) for name in PYTHON_SDK_MODULES]

        return {
            "decorator_docs": _generate_decorator_docs(),
            "context_docs": _generate_context_docs(),
            "error_docs": _generate_error_docs(),
            "sdk_module_docs": "\n".join(module_lines),
            "sdk_models_docs": _generate_models_docs(),
        }
    except ImportError as e:
        logger.warning(f"Could not generate SDK tokens: {e}")
        return {
            "decorator_docs": "",
            "context_docs": "",
            "error_docs": "",
            "sdk_module_docs": "",
            "sdk_models_docs": "",
        }


def _generate_model_tokens() -> dict[str, str]:
    """Generate Pydantic model documentation tokens."""
    from src.services.mcp_server.schema_utils import models_to_markdown
    from src.models.contracts.forms import (
        FormCreate, FormUpdate, FormSchema, FormField,
    )
    from src.models.contracts.agents import AgentCreate, AgentUpdate
    from src.models.contracts.tables import TableCreate, TableUpdate

    form_docs = models_to_markdown([
        (FormCreate, "FormCreate"),
        (FormUpdate, "FormUpdate"),
        (FormSchema, "FormSchema"),
        (FormField, "FormField"),
    ], "Form Models")

    agent_docs = models_to_markdown([
        (AgentCreate, "AgentCreate"),
        (AgentUpdate, "AgentUpdate"),
    ], "Agent Models")

    table_docs = models_to_markdown([
        (TableCreate, "TableCreate"),
        (TableUpdate, "TableUpdate"),
    ], "Table Models")

    from bifrost.manifest import (
        ManifestWorkflow, ManifestForm, ManifestAgent, ManifestApp,
        ManifestIntegration, ManifestConfig, ManifestTable,
        ManifestEventSource, ManifestEventSubscription,
    )

    manifest_docs = models_to_markdown([
        (ManifestWorkflow, "ManifestWorkflow — .bifrost/workflows.yaml entry"),
        (ManifestForm, "ManifestForm — .bifrost/forms.yaml entry"),
        (ManifestAgent, "ManifestAgent — .bifrost/agents.yaml entry"),
        (ManifestApp, "ManifestApp — .bifrost/apps.yaml entry"),
        (ManifestTable, "ManifestTable — .bifrost/tables.yaml entry"),
        (ManifestIntegration, "ManifestIntegration — .bifrost/integrations.yaml entry"),
        (ManifestConfig, "ManifestConfig — .bifrost/configs.yaml entry"),
        (ManifestEventSource, "ManifestEventSource — .bifrost/events.yaml entry"),
        (ManifestEventSubscription, "ManifestEventSubscription — subscription within an event source"),
    ], "Manifest YAML Models (Configuration as Code)")

    return {
        "form_model_docs": form_docs,
        "agent_model_docs": agent_docs,
        "table_model_docs": table_docs,
        "manifest_docs": manifest_docs,
    }


def generate_llms_txt() -> str:
    """Read the template and fill all tokens. Regenerated per request."""
    template = _TEMPLATE_PATH.read_text()

    tokens: dict[str, str] = {}
    tokens.update(_generate_sdk_tokens())
    tokens.update(_generate_model_tokens())

    for key, value in tokens.items():
        template = template.replace("{" + key + "}", value)

    return template
