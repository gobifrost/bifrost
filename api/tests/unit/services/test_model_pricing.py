"""Tests for provider identity, usage model names, and catalog price fill-in."""

from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.models.orm.ai_usage import AIModelPricing
from src.services.model_catalog import parse_catalog
from src.services.llm.base import LLMConfig
from src.services.model_pricing import (
    canonical_provider,
    chain_context_window,
    fill_pricing_from_catalog,
    is_openrouter_endpoint,
    usage_model_name,
)

CATALOG = parse_catalog(
    {
        "anthropic": {
            "id": "anthropic",
            "name": "Anthropic",
            "npm": "@ai-sdk/anthropic",
            "models": {
                "claude-opus-4-5": {
                    "id": "claude-opus-4-5",
                    "name": "Claude Opus 4.5",
                    "cost": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25},
                    "limit": {"context": 200000},
                }
            },
        },
        "fireworks-ai": {
            "id": "fireworks-ai",
            "name": "Fireworks AI",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://api.fireworks.ai/inference/v1/",
            "models": {
                "accounts/fireworks/models/kimi-k3": {
                    "id": "accounts/fireworks/models/kimi-k3",
                    "name": "Kimi K3",
                    "cost": {"input": 0.6, "output": 2.5},
                    "limit": {"context": 131072},
                },
                "free-preview": {"id": "free-preview", "name": "Preview"},
            },
        },
    },
    fetched_at=None,
    source="bundled",
)


@pytest.fixture
def catalog():
    with patch("src.services.model_pricing.get_model_catalog", return_value=CATALOG):
        yield CATALOG


def test_openrouter_identity_is_derived_from_endpoint_without_contract_change() -> None:
    assert is_openrouter_endpoint("https://openrouter.ai/api/v1")
    assert canonical_provider("openai", "https://openrouter.ai/api/v1") == "openrouter"
    assert canonical_provider("custom", "https://gateway.example/v1") == "openai"


def test_usage_names_keep_each_provider_historical_convention() -> None:
    # Native providers key usage and prices by the undated id, as before.
    assert usage_model_name("anthropic", "claude-opus-4-5-20251101") == "claude-opus-4-5"
    assert usage_model_name("openai", "gpt-4o-2024-11-20") == "gpt-4o"
    assert usage_model_name("google", "gemini-3.8-flash") == "gemini-3.8-flash"
    # OpenRouter and community providers keep the exact id.
    assert usage_model_name("openrouter", "anthropic/claude-opus-4-5") == (
        "anthropic/claude-opus-4-5"
    )
    assert usage_model_name("fireworks-ai", "model-2026-01-01") == "model-2026-01-01"


@pytest.mark.asyncio
async def test_missing_price_is_filled_from_catalog_for_any_provider(db_session, catalog) -> None:
    added = await fill_pricing_from_catalog(
        db_session,
        provider="fireworks-ai",
        model="accounts/fireworks/models/kimi-k3",
        usage_name="accounts/fireworks/models/kimi-k3",
    )
    assert added is True
    row = (
        await db_session.execute(
            select(AIModelPricing).where(AIModelPricing.provider == "fireworks-ai")
        )
    ).scalar_one()
    assert row.input_price_per_million == Decimal("0.6000")
    assert row.output_price_per_million == Decimal("2.5000")
    assert row.cache_read_price_per_million is None


@pytest.mark.asyncio
async def test_fill_never_overwrites_an_existing_price(db_session, catalog) -> None:
    db_session.add(
        AIModelPricing(
            provider="anthropic",
            model="claude-opus-4-5",
            input_price_per_million=Decimal("4.0000"),
            output_price_per_million=Decimal("20.0000"),
        )
    )
    await db_session.flush()

    added = await fill_pricing_from_catalog(
        db_session,
        provider="anthropic",
        model="claude-opus-4-5-20251101",
        usage_name="claude-opus-4-5",
    )

    assert added is False
    row = (
        await db_session.execute(
            select(AIModelPricing).where(AIModelPricing.model == "claude-opus-4-5")
        )
    ).scalar_one()
    assert row.input_price_per_million == Decimal("4.0000")


@pytest.mark.asyncio
async def test_models_without_published_prices_are_left_unpriced(db_session, catalog) -> None:
    assert not await fill_pricing_from_catalog(
        db_session, provider="fireworks-ai", model="free-preview", usage_name="free-preview"
    )
    assert not await fill_pricing_from_catalog(
        db_session, provider="unknown-cloud", model="m", usage_name="m"
    )


@pytest.mark.asyncio
async def test_display_names_cover_usage_prices_and_profiles(db_session, catalog) -> None:
    from sqlalchemy import delete

    from src.models.orm.ai_usage import AIUsage
    from src.services.model_pricing import used_model_display_names

    # Start from known rows only; the shared test DB may hold others.
    await db_session.execute(delete(AIUsage))
    await db_session.execute(delete(AIModelPricing))
    db_session.add_all(
        [
            AIModelPricing(
                provider="fireworks-ai",
                model="accounts/fireworks/models/kimi-k3",
                input_price_per_million=Decimal("1"),
                output_price_per_million=Decimal("1"),
            ),
            AIModelPricing(
                provider="anthropic",
                model="claude-opus-4-5",
                input_price_per_million=Decimal("1"),
                output_price_per_million=Decimal("1"),
            ),
            AIModelPricing(
                provider="openai",
                model="not-in-catalog",
                input_price_per_million=Decimal("1"),
                output_price_per_million=Decimal("1"),
            ),
        ]
    )
    await db_session.flush()

    names = await used_model_display_names(db_session)

    assert names["accounts/fireworks/models/kimi-k3"] == "Kimi K3"
    assert names["claude-opus-4-5"] == "Claude Opus 4.5"
    assert "not-in-catalog" not in names


def _config(provider: str, model: str, catalog_provider_id: str | None = None) -> LLMConfig:
    return LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        api_key="test",
        catalog_provider_id=catalog_provider_id,
    )


@pytest.mark.asyncio
async def test_chain_context_window_is_the_smallest_known_window(catalog) -> None:
    configs = [
        _config("anthropic", "claude-opus-4-5-20251101"),
        _config("openai", "accounts/fireworks/models/kimi-k3", "fireworks-ai"),
        _config("openai", "unknown-model"),
    ]

    assert await chain_context_window(None, configs) == 131_072  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_chain_context_window_uses_the_primary_model_override(catalog) -> None:
    configs = [_config("anthropic", "unknown-model")]

    assert await chain_context_window(None, configs) is None  # type: ignore[arg-type]
    assert (
        await chain_context_window(
            None,  # type: ignore[arg-type]
            configs,
            primary_model="claude-opus-4-5",
        )
        == 200_000
    )
