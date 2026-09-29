from src.models.contracts.artifacts import ModelCapabilities
from src.services.model_capabilities import (
    catalog_capabilities,
    model_fingerprint,
    normalize_capabilities,
    should_offer_tool_calling,
)
from src.services.model_catalog import CatalogModel


def test_catalog_capabilities_follow_published_modalities_and_tools() -> None:
    entry = CatalogModel(
        id="claude-haiku-4-5",
        name="Claude Haiku 4.5",
        tool_call=True,
        input_modalities=["text", "image", "pdf"],
    )

    capabilities = catalog_capabilities(
        entry, provider="anthropic", model="claude-haiku-4-5", endpoint=None
    )

    assert (capabilities.image_input, capabilities.pdf_input, capabilities.tool_calling) == (
        True,
        True,
        True,
    )
    assert capabilities.source == "catalog"
    # The fingerprint binds the record to this exact target.
    assert capabilities.fingerprint == model_fingerprint(
        provider="anthropic", model="claude-haiku-4-5", endpoint=None
    )


def test_catalog_without_tool_support_suppresses_tools() -> None:
    entry = CatalogModel(id="m", name="M", tool_call=False)
    capabilities = catalog_capabilities(entry, provider="openai", model="m", endpoint=None)
    assert should_offer_tool_calling(capabilities) is False


def test_stale_capability_fingerprint_is_not_reused() -> None:
    stale = ModelCapabilities(
        tool_calling=True,
        source="manual",
        fingerprint=model_fingerprint(
            provider="openai", model="old-model", endpoint=None
        ),
    )

    normalized = normalize_capabilities(
        stale,
        provider="openai",
        model="new-model",
        endpoint=None,
    )

    assert normalized.source == "unknown"
    assert normalized.tool_calling is False
    assert normalized.fingerprint != stale.fingerprint


def test_unknown_capabilities_are_optimistic_for_tool_calling() -> None:
    unknown = ModelCapabilities(source="unknown", tool_calling=False)
    verified_unsupported = ModelCapabilities(source="verified", tool_calling=False)
    manual_unsupported = ModelCapabilities(source="manual", tool_calling=False)
    verified_supported = ModelCapabilities(source="verified", tool_calling=True)

    assert should_offer_tool_calling(unknown) is True
    assert should_offer_tool_calling(verified_unsupported) is False
    assert should_offer_tool_calling(manual_unsupported) is False
    assert should_offer_tool_calling(verified_supported) is True
