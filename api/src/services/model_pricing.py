"""Canonical provider identity, usage model names, and catalog lookups."""

import logging
import re
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.ai_models import AIModelProfile, AIProviderConnection
from src.models.orm.ai_usage import AIModelPricing, AIUsage
from src.services.llm.base import LLMConfig
from src.services.model_catalog import (
    NATIVE_CATALOG_IDS,
    CatalogModel,
    ModelCatalog,
    get_model_catalog,
)

logger = logging.getLogger(__name__)

OPENROUTER_PROVIDER = "openrouter"
_DATE_SUFFIX = re.compile(r"-\d{4}(?:-\d{2}-\d{2}|\d{4})$")


def is_openrouter_endpoint(endpoint: str | None) -> bool:
    """Return whether an OpenAI-compatible endpoint is OpenRouter."""

    if not endpoint:
        return False
    hostname = urlparse(endpoint).hostname
    return hostname == "openrouter.ai" or bool(
        hostname and hostname.endswith(".openrouter.ai")
    )


def canonical_provider(provider: str, endpoint: str | None = None) -> str:
    """Resolve the billing provider without changing Bifrost's public config."""

    normalized = provider.strip().lower()
    if normalized == "custom":
        normalized = "openai"
    if normalized == OPENROUTER_PROVIDER or is_openrouter_endpoint(endpoint):
        return OPENROUTER_PROVIDER
    return normalized


def strip_date_suffix(model_id: str) -> str:
    """``claude-opus-4-5-20251101`` -> ``claude-opus-4-5``; ``gpt-4o-2024-11-20`` -> ``gpt-4o``."""

    return _DATE_SUFFIX.sub("", model_id)


def _catalog_entry(
    catalog: ModelCatalog, provider: str, model: str
) -> CatalogModel | None:
    catalog_id = NATIVE_CATALOG_IDS.get(provider, provider)
    return catalog.model(catalog_id, model) or catalog.model(
        catalog_id, strip_date_suffix(model)
    )


async def chain_context_window(
    session: AsyncSession,
    configs: list[LLMConfig],
    *,
    primary_model: str | None = None,
) -> int | None:
    """Smallest catalog context window across a failover chain.

    Any candidate may serve a request, so the chain is governed by the
    smallest window the catalog knows. ``primary_model`` replaces the first
    config's model, matching ``build_chain_model``'s override. Returns None
    when the catalog knows none of the chain's models.
    """

    catalog = await get_model_catalog(session)
    windows: list[int] = []
    for index, config in enumerate(configs):
        model = primary_model if index == 0 and primary_model else config.model
        entry = (
            catalog.model(config.catalog_provider_id, model)
            if config.catalog_provider_id
            else None
        )
        if entry is None:
            entry = _catalog_entry(
                catalog, canonical_provider(config.provider, config.endpoint), model
            )
        if entry is not None and entry.context_window:
            windows.append(entry.context_window)
    return min(windows) if windows else None


def usage_model_name(provider: str, model: str) -> str:
    """Name a usage row and its price under, stable across model snapshots.

    Native providers' rows have always been keyed by the model id without
    its date suffix (``claude-sonnet-4-6``, ``gpt-4o``), so a new dated
    snapshot shares its predecessor's usage and price. OpenRouter and
    community providers keep the exact model id.
    """

    if provider in NATIVE_CATALOG_IDS and provider != OPENROUTER_PROVIDER:
        return strip_date_suffix(model)
    return model


async def fill_pricing_from_catalog(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    usage_name: str,
) -> bool:
    """Add the catalog price for a model that has none. Never overwrites.

    Prices an administrator entered, or that were filled earlier, always win.
    Returns whether a price row was added.
    """

    catalog = await get_model_catalog(session)
    entry = _catalog_entry(catalog, provider, model)
    cost = entry.cost if entry else None
    if cost is None or cost.input is None or cost.output is None:
        return False
    result = await session.execute(
        insert(AIModelPricing)
        .values(
            provider=provider,
            model=usage_name,
            input_price_per_million=cost.input,
            output_price_per_million=cost.output,
            cache_read_price_per_million=cost.cache_read,
            cache_write_price_per_million=cost.cache_write,
            effective_date=date.today(),
        )
        .on_conflict_do_nothing(constraint="uq_ai_model_pricing_provider_model")
        .returning(AIModelPricing.id)
    )
    added = result.scalar_one_or_none() is not None
    if added:
        logger.info("Filled %s/%s pricing from the model catalog", provider, usage_name)
    return added


async def used_model_display_names(session: AsyncSession) -> dict[str, str]:
    """Catalog display names for every model id Bifrost stores or configures.

    Keys are the stored ids (usage rows, prices, profiles); ids the catalog
    does not know are omitted, so callers show the id itself.
    """
    catalog = await get_model_catalog(session)
    pairs: set[tuple[str, str]] = set()
    for model_table in (AIUsage, AIModelPricing):
        rows = await session.execute(
            select(model_table.provider, model_table.model).distinct()
        )
        pairs.update((provider, model) for provider, model in rows.all())
    profiles = await session.execute(
        select(
            AIProviderConnection.catalog_provider_id,
            AIProviderConnection.provider,
            AIModelProfile.model,
        ).join(AIModelProfile, AIModelProfile.connection_id == AIProviderConnection.id)
    )
    names: dict[str, str] = {}
    for catalog_id, kind, model in profiles.all():
        entry = catalog.model(catalog_id, model) if catalog_id else None
        if entry is None:
            entry = _catalog_entry(catalog, kind, model)
        if entry is not None:
            names[model] = entry.name
    for provider, model in pairs:
        entry = _catalog_entry(catalog, provider, model)
        if entry is not None:
            names.setdefault(model, entry.name)
    return names
