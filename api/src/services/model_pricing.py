"""Canonical provider identity and provider-published model pricing."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx
import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.ai_usage import AIModelPricing, AIUsage

logger = logging.getLogger(__name__)

OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
PRICING_DISCOVERY_KEY_PREFIX = "ai_pricing_discovery:"
PRICING_DISCOVERY_TTL = 300


@dataclass(frozen=True)
class PublishedModelPricing:
    """Per-million token prices published by a provider catalog."""

    input_price: Decimal
    output_price: Decimal
    cache_read_price: Decimal | None = None
    cache_write_price: Decimal | None = None


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


def configured_models(*models: str | None) -> set[str]:
    """Return every distinct non-empty configured model identifier."""

    return {model.strip() for model in models if model and model.strip()}


def _per_million(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if value < 0 or value > Decimal("999999.9999"):
        return None
    return value.quantize(Decimal("0.0001"))


def parse_pricing_catalog(body: object) -> dict[str, PublishedModelPricing]:
    """Parse an OpenAI-compatible ``/models`` response without trusting shape."""

    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return {}
    result: dict[str, PublishedModelPricing] = {}
    for item in body["data"]:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        pricing = item.get("pricing")
        if not isinstance(model_id, str) or not isinstance(pricing, dict):
            continue
        input_price = _per_million(pricing.get("prompt"))
        output_price = _per_million(pricing.get("completion"))
        if input_price is None or output_price is None:
            continue
        result[model_id] = PublishedModelPricing(
            input_price=input_price,
            output_price=output_price,
            cache_read_price=_per_million(pricing.get("input_cache_read")),
            cache_write_price=_per_million(pricing.get("input_cache_write")),
        )
    return result


async def fetch_pricing_catalog(
    endpoint: str,
    *,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, PublishedModelPricing]:
    """Fetch pricing published by a provider's model catalog."""

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    async with httpx.AsyncClient(timeout=timeout_seconds) as http:
        response = await http.get(endpoint, headers=headers)
        response.raise_for_status()
    return parse_pricing_catalog(response.json())


async def sync_published_pricing(
    session: AsyncSession,
    *,
    provider: str,
    selected_models: Iterable[str],
    catalog: dict[str, PublishedModelPricing],
) -> int:
    """Update known prices and add selected/used catalog models."""

    provider = canonical_provider(provider)
    if not catalog:
        return 0

    existing_result = await session.execute(
        select(AIModelPricing).where(AIModelPricing.provider == provider)
    )
    existing = {row.model: row for row in existing_result.scalars().all()}
    changed = 0
    now = datetime.now(timezone.utc)

    for model_id, row in existing.items():
        published = catalog.get(model_id)
        if published is None:
            continue
        next_values = (
            published.input_price,
            published.output_price,
            published.cache_read_price,
            published.cache_write_price,
        )
        current_values = (
            row.input_price_per_million,
            row.output_price_per_million,
            row.cache_read_price_per_million,
            row.cache_write_price_per_million,
        )
        if current_values != next_values:
            row.input_price_per_million = published.input_price
            row.output_price_per_million = published.output_price
            row.cache_read_price_per_million = published.cache_read_price
            row.cache_write_price_per_million = published.cache_write_price
            row.updated_at = now
            changed += 1

    used_result = await session.execute(
        select(AIUsage.model).where(AIUsage.provider == provider).distinct()
    )
    requested = configured_models(*selected_models)
    requested.update(row[0] for row in used_result.all())
    for model_id in sorted(requested - existing.keys()):
        published = catalog.get(model_id)
        if published is None:
            continue
        session.add(
            AIModelPricing(
                provider=provider,
                model=model_id,
                input_price_per_million=published.input_price,
                output_price_per_million=published.output_price,
                cache_read_price_per_million=published.cache_read_price,
                cache_write_price_per_million=published.cache_write_price,
                effective_date=date.today(),
            )
        )
        changed += 1

    await session.flush()
    return changed


async def discover_openrouter_pricing(
    session: AsyncSession,
    redis_client: redis.Redis,
    model: str,
) -> bool:
    """Best-effort, deduplicated catalog recovery for one genuine price miss."""

    lock_key = f"{PRICING_DISCOVERY_KEY_PREFIX}{OPENROUTER_PROVIDER}:{model}"
    try:
        acquired = await redis_client.set(
            lock_key,
            "1",
            ex=PRICING_DISCOVERY_TTL,
            nx=True,
        )
    except Exception as exc:
        logger.warning("Pricing discovery deduplication failed: %s", exc)
        acquired = True
    if not acquired:
        return False

    try:
        catalog = await fetch_pricing_catalog(
            OPENROUTER_MODELS_ENDPOINT,
            timeout_seconds=5.0,
        )
        changed = await sync_published_pricing(
            session,
            provider=OPENROUTER_PROVIDER,
            selected_models={model},
            catalog=catalog,
        )
        return changed > 0
    except Exception as exc:
        logger.warning("OpenRouter pricing discovery failed for %s: %s", model, exc)
        return False
