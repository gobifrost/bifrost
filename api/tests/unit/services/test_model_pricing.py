"""Tests for provider identity and provider-published pricing discovery."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.model_pricing import (
    OPENROUTER_MODELS_ENDPOINT,
    PublishedModelPricing,
    canonical_provider,
    configured_models,
    discover_openrouter_pricing,
    is_openrouter_endpoint,
    parse_pricing_catalog,
    sync_published_pricing,
)


def test_openrouter_identity_is_derived_from_endpoint_without_contract_change() -> None:
    assert is_openrouter_endpoint("https://openrouter.ai/api/v1")
    assert canonical_provider("openai", "https://openrouter.ai/api/v1") == "openrouter"
    assert canonical_provider("custom", "https://gateway.example/v1") == "openai"
    assert configured_models(" balanced ", "fast", None, "fast") == {
        "balanced",
        "fast",
    }


def test_catalog_parser_preserves_zero_and_cache_prices() -> None:
    catalog = parse_pricing_catalog(
        {
            "data": [
                {
                    "id": "deepseek/deepseek-v4-flash",
                    "pricing": {
                        "prompt": "0.0000002",
                        "completion": "0.0000008",
                        "input_cache_read": "0.00000002",
                        "input_cache_write": "0",
                    },
                },
                {"id": "missing-pricing"},
            ]
        }
    )

    assert catalog["deepseek/deepseek-v4-flash"] == PublishedModelPricing(
        input_price=Decimal("0.2000"),
        output_price=Decimal("0.8000"),
        cache_read_price=Decimal("0.0200"),
        cache_write_price=Decimal("0.0000"),
    )
    assert "missing-pricing" not in catalog


@pytest.mark.asyncio
async def test_sync_adds_all_selected_and_previously_used_models() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    existing_result = MagicMock()
    existing_result.scalars.return_value.all.return_value = []
    used_result = MagicMock()
    used_result.all.return_value = [("used/model",)]
    session.execute.side_effect = [existing_result, used_result]
    pricing = PublishedModelPricing(Decimal("1"), Decimal("2"), Decimal("0.1"))
    catalog = {
        "balanced/model": pricing,
        "fast/model": pricing,
        "pro/model": pricing,
        "summary/model": pricing,
        "tuning/model": pricing,
        "used/model": pricing,
    }

    changed = await sync_published_pricing(
        session,
        provider="openrouter",
        selected_models={
            "balanced/model",
            "fast/model",
            "pro/model",
            "summary/model",
            "tuning/model",
        },
        catalog=catalog,
    )

    assert changed == 6
    assert {call.args[0].model for call in session.add.call_args_list} == set(catalog)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_discovery_is_deduplicated_and_uses_public_openrouter_catalog() -> None:
    session = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = True
    catalog = {
        "new/model": PublishedModelPricing(Decimal("1"), Decimal("2")),
    }
    with (
        patch(
            "src.services.model_pricing.fetch_pricing_catalog",
            new=AsyncMock(return_value=catalog),
        ) as fetch,
        patch(
            "src.services.model_pricing.sync_published_pricing",
            new=AsyncMock(return_value=1),
        ) as sync,
    ):
        assert await discover_openrouter_pricing(session, redis, "new/model")

    fetch.assert_awaited_once_with(OPENROUTER_MODELS_ENDPOINT, timeout_seconds=5.0)
    sync.assert_awaited_once_with(
        session,
        provider="openrouter",
        selected_models={"new/model"},
        catalog=catalog,
    )
    redis.set.assert_awaited_once()

    redis.set.reset_mock()
    redis.set.return_value = False
    with patch(
        "src.services.model_pricing.fetch_pricing_catalog",
        new=AsyncMock(),
    ) as fetch_again:
        assert not await discover_openrouter_pricing(session, redis, "new/model")
    fetch_again.assert_not_awaited()
