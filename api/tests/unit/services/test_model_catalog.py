"""Tests for the models.dev-backed provider and model catalog."""

import gzip
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import delete

from src.models.orm.ai_models import AIModelCatalog
from src.services import model_catalog
from src.services.model_catalog import (
    CATALOG_SOURCE,
    CatalogRefreshRejected,
    get_model_catalog,
    load_bundled_catalog,
    parse_catalog,
    refresh_model_catalog,
    reset_catalog_cache,
    trim_catalog,
)


def _provider(pid: str, npm: str, api: str | None, models: dict) -> dict:
    entry = {"id": pid, "name": pid.title(), "npm": npm, "env": [f"{pid.upper()}_API_KEY"], "models": models}
    if api:
        entry["api"] = api
    return entry


def _raw_catalog(extra_models: int = 0) -> dict:
    """A models.dev-shaped payload with enough providers to pass sanity checks."""
    raw = {
        "anthropic": _provider(
            "anthropic",
            "@ai-sdk/anthropic",
            None,
            {
                "claude-haiku-4-5": {
                    "id": "claude-haiku-4-5",
                    "name": "Claude Haiku 4.5",
                    "reasoning": True,
                    "reasoning_options": [{"type": "budget_tokens", "min": 1024}],
                    "tool_call": True,
                    "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                    "limit": {"context": 200000, "output": 64000},
                    "cost": {"input": 1, "output": 5, "cache_read": 0.1, "cache_write": 1.25},
                    "knowledge": "2025-02",  # dropped by trimming
                }
            },
        ),
        "deepseek": _provider(
            "deepseek",
            "@ai-sdk/openai-compatible",
            "https://api.deepseek.com",
            {
                "deepseek-v4": {
                    "id": "deepseek-v4",
                    "name": "DeepSeek V4",
                    "reasoning": True,
                    "reasoning_options": [
                        {"type": "toggle"},
                        {"type": "effort", "values": ["low", "high", "max"]},
                    ],
                }
            },
        ),
        "bedrock": _provider("bedrock", "@ai-sdk/amazon-bedrock", None, {"m": {"id": "m", "name": "M"}}),
        # Own SDK package with the base URL built in, so no "api" is published.
        "groq": _provider("groq", "@ai-sdk/groq", None, {"llama": {"id": "llama", "name": "Llama"}}),
        "broken": "not a provider",
    }
    for index in range(25 + extra_models):
        raw[f"community-{index:02d}"] = _provider(
            f"community-{index:02d}",
            "@ai-sdk/openai-compatible",
            f"https://c{index}.example/v1",
            {f"model-{index}": {"id": f"model-{index}", "name": f"Model {index}"}},
        )
    return raw


def test_trim_keeps_only_used_fields_and_drops_malformed_entries() -> None:
    trimmed = trim_catalog(_raw_catalog())
    assert "broken" not in trimmed
    haiku = trimmed["anthropic"]["models"]["claude-haiku-4-5"]
    assert "knowledge" not in haiku
    assert haiku["cost"]["cache_write"] == 1.25


def test_adapters_follow_each_providers_sdk() -> None:
    catalog = parse_catalog(trim_catalog(_raw_catalog()), fetched_at=None, source="bundled")
    assert catalog.provider("anthropic").adapter == "anthropic"
    assert catalog.provider("anthropic").endpoint == "https://api.anthropic.com"
    assert catalog.provider("deepseek").adapter == "openai_compatible"
    assert catalog.provider("deepseek").is_native is False
    # A provider with its own SDK and no OpenAI-style endpoint is unsupported.
    assert catalog.provider("bedrock").adapter is None
    supported = [p.id for p in catalog.supported_providers()]
    assert supported[0] == "anthropic"  # native providers sort first
    assert "bedrock" not in supported


def test_known_openai_compatible_providers_get_a_base_url() -> None:
    catalog = parse_catalog(trim_catalog(_raw_catalog()), fetched_at=None, source="bundled")
    groq = catalog.provider("groq")
    assert groq.adapter == "openai_compatible"
    assert groq.endpoint == "https://api.groq.com/openai/v1"
    route = groq.route(groq.models["llama"])
    assert route is not None
    assert (route.provider, route.endpoint, route.openai_transport) == (
        "openai",
        "https://api.groq.com/openai/v1",
        "chat_completions",
    )
    assert "groq" in [p.id for p in catalog.supported_providers()]


def test_a_published_catalog_base_url_wins_over_the_table() -> None:
    raw = _raw_catalog()
    raw["groq"]["api"] = "https://groq.example/v2"
    groq = parse_catalog(trim_catalog(raw), fetched_at=None, source="bundled").provider("groq")
    assert groq.endpoint == "https://groq.example/v2"
    assert groq.route(groq.models["llama"]).endpoint == "https://groq.example/v2"


def test_bundled_snapshot_offers_every_tabled_provider() -> None:
    catalog = load_bundled_catalog()
    supported = {p.id for p in catalog.supported_providers()}
    for pid in model_catalog.OPENAI_COMPATIBLE_ENDPOINTS:
        assert pid in supported, pid
        provider = catalog.provider(pid)
        assert any(provider.route(m) for m in provider.models.values()), pid


def test_reasoning_choices_come_from_the_catalog_options() -> None:
    catalog = parse_catalog(trim_catalog(_raw_catalog()), fetched_at=None, source="bundled")
    assert catalog.model("deepseek", "deepseek-v4").reasoning_choices == [
        "off",
        "on",
        "low",
        "high",
        "max",
    ]
    # Budget-only models get on/off; the adapter picks the budget.
    assert catalog.model("anthropic", "claude-haiku-4-5").reasoning_choices == ["off", "on"]
    assert catalog.model("community-00", "model-0").reasoning_choices == []


def test_bundled_snapshot_is_a_usable_catalog() -> None:
    catalog = load_bundled_catalog()
    assert len(catalog.supported_providers()) > 150
    for pid in ("openai", "anthropic", "google", "openrouter", "opencode-go"):
        assert catalog.provider(pid) is not None, pid
        assert catalog.provider(pid).is_native
    # The shipped file is already in trimmed form, so a refresh and the
    # snapshot store the same shape.
    with gzip.open(model_catalog.SNAPSHOT_PATH, "rt", encoding="utf-8") as snapshot:
        payload = json.load(snapshot)
    assert trim_catalog(payload) == payload


@pytest.fixture
async def empty_store(db_session, monkeypatch):
    """No stored catalog, and a small bundled baseline to compare against.

    The test stack's scheduler refreshes the real catalog at startup, so any
    stored row is removed inside this test's transaction.
    """
    await db_session.execute(delete(AIModelCatalog))
    small = parse_catalog(trim_catalog(_raw_catalog()), fetched_at=None, source="bundled")
    monkeypatch.setattr(model_catalog, "load_bundled_catalog", lambda: small)
    reset_catalog_cache()
    yield db_session
    reset_catalog_cache()


def _mock_source(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", client)


@pytest.mark.asyncio
async def test_refresh_stores_catalog_and_reuses_it_until_it_changes(empty_store, monkeypatch) -> None:
    db_session = empty_store
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json=_raw_catalog(), headers={"etag": '"v1"'})

    _mock_source(monkeypatch, handler)
    first = await refresh_model_catalog(db_session)
    assert first.changed is True
    second = await refresh_model_catalog(db_session)
    assert second.changed is False
    assert calls == [None, '"v1"']

    reset_catalog_cache()
    catalog = await get_model_catalog(db_session)
    assert catalog.source == "refreshed"
    assert catalog.model("deepseek", "deepseek-v4") is not None


@pytest.mark.asyncio
async def test_implausibly_small_refresh_keeps_last_good_catalog(empty_store, monkeypatch) -> None:
    db_session = empty_store
    good = trim_catalog(_raw_catalog(extra_models=40))
    now = datetime.now(timezone.utc)
    db_session.add(
        AIModelCatalog(
            source=CATALOG_SOURCE,
            etag='"good"',
            payload=good,
            provider_count=len(good),
            model_count=sum(len(p["models"]) for p in good.values()),
            fetched_at=now,
            checked_at=now,
        )
    )
    await db_session.flush()
    _mock_source(monkeypatch, lambda request: httpx.Response(200, json=_raw_catalog()))

    with pytest.raises(CatalogRefreshRejected):
        await refresh_model_catalog(db_session)

    row = await db_session.get(AIModelCatalog, CATALOG_SOURCE)
    assert row.etag == '"good"'
    assert row.payload == good


@pytest.mark.asyncio
async def test_unreachable_source_raises_without_touching_storage(empty_store, monkeypatch) -> None:
    db_session = empty_store
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    _mock_source(monkeypatch, handler)
    with pytest.raises(httpx.HTTPError):
        await refresh_model_catalog(db_session)
    assert await db_session.get(AIModelCatalog, CATALOG_SOURCE) is None
