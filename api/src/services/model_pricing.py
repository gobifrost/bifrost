"""Canonical provider identity, usage model names, and catalog price fill-in."""

import logging
import re
from datetime import date
from urllib.parse import urlparse

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.ai_usage import AIModelPricing
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

