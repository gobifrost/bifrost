"""Provider and model catalog backed by models.dev.

models.dev is a community-maintained, MIT-licensed catalog of AI providers
and their models: endpoints, context limits, per-provider prices, and which
reasoning controls each model accepts. Bifrost uses it the way other agent
harnesses do (OpenCode, Hermes, Cline) instead of calling every provider's
``/models`` endpoint:

1. A trimmed snapshot ships with the release, so the catalog is never empty.
2. A recurring platform job refreshes it (``model_catalog.refresh``) with an
   ETag, keeping the last good copy in Postgres.
3. A refresh that returns an empty or sharply smaller catalog is rejected and
   the last good copy stays in use.

Provider ``/models`` endpoints are still used for custom endpoints that are
not in the catalog.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.ai_models import AIModelCatalog

if TYPE_CHECKING:
    from src.models.contracts.ai_models import ModelCatalogResponse

logger = logging.getLogger(__name__)

CATALOG_SOURCE = "models.dev"
MODELS_DEV_URL = "https://models.dev/api.json"
SNAPSHOT_PATH = Path(__file__).with_name("model_catalog_snapshot.json.gz")
# How long a process trusts its parsed copy before checking for a newer row.
_RELOAD_CHECK_SECONDS = 60.0
# A refreshed catalog smaller than this share of the last good copy is
# treated as a broken upstream response rather than real removals.
_MIN_MODEL_RATIO = 0.5
_MIN_PROVIDERS = 20

AdapterKind = Literal["openai", "anthropic", "google", "openrouter", "openai_compatible", "opencode_go"]

# Providers Bifrost exercises directly. Every other catalog provider reuses
# one of these adapters and is presented as a community provider.
NATIVE_PROVIDER_KINDS: dict[str, AdapterKind] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "openrouter": "openrouter",
    "opencode-go": "opencode_go",
}
# Bifrost connection kind -> its native catalog provider id.
NATIVE_CATALOG_IDS: dict[str, str] = {kind: pid for pid, kind in NATIVE_PROVIDER_KINDS.items()}
NATIVE_DEFAULT_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
}
# Catalog SDK package -> the Bifrost adapter that speaks the same wire API.
_ADAPTER_BY_SDK: dict[str, AdapterKind] = {
    "@ai-sdk/openai-compatible": "openai_compatible",
    "@ai-sdk/openai": "openai",
    "@ai-sdk/anthropic": "anthropic",
    "@ai-sdk/google": "google",
    "@openrouter/ai-sdk-provider": "openrouter",
}
_MODEL_FIELDS = (
    "id",
    "name",
    "family",
    "reasoning",
    "reasoning_options",
    "tool_call",
    "attachment",
    "modalities",
    "limit",
    "cost",
    "status",
)
_PROVIDER_FIELDS = ("id", "name", "api", "npm", "doc", "env")


class ReasoningOption(BaseModel):
    """One way a model accepts reasoning control, as published by models.dev."""

    type: str
    values: list[str] = Field(default_factory=list)
    min: int | None = None
    max: int | None = None


class CatalogCost(BaseModel):
    """USD per million tokens."""

    input: Decimal | None = None
    output: Decimal | None = None
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None


class CatalogModel(BaseModel):
    id: str
    name: str
    family: str | None = None
    reasoning: bool = False
    reasoning_options: list[ReasoningOption] = Field(default_factory=list)
    tool_call: bool = False
    attachment: bool = False
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    context_window: int | None = None
    max_output_tokens: int | None = None
    cost: CatalogCost | None = None
    status: str | None = None

    @property
    def reasoning_choices(self) -> list[str]:
        """Values a profile may select, in the catalog's order.

        Effort levels are offered as-is. A toggle adds explicit "on"/"off".
        Token-budget-only models get "on"/"off"; the adapter picks the budget.
        """
        if not self.reasoning:
            return []
        choices: list[str] = []
        for option in self.reasoning_options:
            if option.type == "effort":
                choices.extend(v for v in option.values if v not in choices)
            elif option.type in ("toggle", "budget_tokens"):
                choices.extend(v for v in ("off", "on") if v not in choices)
        return choices


class CatalogProvider(BaseModel):
    id: str
    name: str
    api: str | None = None
    npm: str | None = None
    doc: str | None = None
    env: list[str] = Field(default_factory=list)
    models: dict[str, CatalogModel] = Field(default_factory=dict)

    @property
    def adapter(self) -> AdapterKind | None:
        """The Bifrost adapter for this provider, or None if unsupported."""
        if self.id in NATIVE_PROVIDER_KINDS:
            return NATIVE_PROVIDER_KINDS[self.id]
        if not self.api:
            return None
        return _ADAPTER_BY_SDK.get(self.npm or "")

    @property
    def is_native(self) -> bool:
        return self.id in NATIVE_PROVIDER_KINDS

    @property
    def endpoint(self) -> str | None:
        return self.api or NATIVE_DEFAULT_ENDPOINTS.get(self.id)


@dataclass(frozen=True)
class ModelCatalog:
    providers: dict[str, CatalogProvider]
    fetched_at: datetime | None
    source: Literal["bundled", "refreshed"]

    def provider(self, provider_id: str | None) -> CatalogProvider | None:
        return self.providers.get(provider_id) if provider_id else None

    def model(self, provider_id: str | None, model_id: str) -> CatalogModel | None:
        provider = self.provider(provider_id)
        return provider.models.get(model_id) if provider else None

    def supported_providers(self) -> list[CatalogProvider]:
        """Providers Bifrost can connect to, native first, then by name."""
        supported = [p for p in self.providers.values() if p.adapter is not None]
        return sorted(supported, key=lambda p: (not p.is_native, p.name.lower()))

    @property
    def model_count(self) -> int:
        return sum(len(p.models) for p in self.providers.values())


def catalog_summary(catalog: ModelCatalog) -> ModelCatalogResponse:
    """Public view of the providers Bifrost can connect to."""
    from src.models.contracts.ai_models import ModelCatalogProvider, ModelCatalogResponse

    providers = []
    for entry in catalog.supported_providers():
        assert entry.adapter is not None
        providers.append(
            ModelCatalogProvider(
                id=entry.id,
                name=entry.name,
                adapter=entry.adapter,
                endpoint=entry.endpoint,
                doc=entry.doc,
                env=entry.env,
                native=entry.is_native,
                model_count=len(entry.models),
            )
        )
    return ModelCatalogResponse(
        source=catalog.source,
        fetched_at=catalog.fetched_at,
        provider_count=len(catalog.providers),
        model_count=catalog.model_count,
        providers=providers,
    )


def trim_catalog(raw: object) -> dict[str, Any]:
    """Keep only the fields Bifrost uses, dropping malformed entries.

    Output is the stored and bundled form: ``{provider_id: provider}`` with
    each provider's ``models`` keyed by model id.
    """
    if not isinstance(raw, dict):
        return {}
    trimmed: dict[str, Any] = {}
    for provider_id, provider in raw.items():
        if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
            continue
        entry = {key: provider[key] for key in _PROVIDER_FIELDS if key in provider}
        entry["id"] = provider_id
        entry["name"] = provider.get("name") or provider_id
        models: dict[str, Any] = {}
        for model_id, model in provider["models"].items():
            if not isinstance(model, dict):
                continue
            kept = {key: model[key] for key in _MODEL_FIELDS if key in model}
            kept["id"] = model_id
            kept["name"] = model.get("name") or model_id
            models[model_id] = kept
        entry["models"] = models
        trimmed[provider_id] = entry
    return trimmed


def parse_catalog(
    payload: dict[str, Any],
    *,
    fetched_at: datetime | None,
    source: Literal["bundled", "refreshed"],
) -> ModelCatalog:
    providers: dict[str, CatalogProvider] = {}
    for provider_id, provider in payload.items():
        models: dict[str, CatalogModel] = {}
        for model_id, model in provider.get("models", {}).items():
            try:
                models[model_id] = _parse_model(model)
            except (ValueError, TypeError) as exc:
                logger.debug("Skipping catalog model %s/%s: %s", provider_id, model_id, exc)
        providers[provider_id] = CatalogProvider(
            id=provider_id,
            name=provider.get("name") or provider_id,
            api=provider.get("api"),
            npm=provider.get("npm"),
            doc=provider.get("doc"),
            env=list(provider.get("env") or []),
            models=models,
        )
    return ModelCatalog(providers=providers, fetched_at=fetched_at, source=source)


def _parse_model(model: dict[str, Any]) -> CatalogModel:
    limit = model.get("limit") or {}
    modalities = model.get("modalities") or {}
    cost = model.get("cost")
    return CatalogModel(
        id=model["id"],
        name=model.get("name") or model["id"],
        family=model.get("family"),
        reasoning=bool(model.get("reasoning")),
        reasoning_options=[
            ReasoningOption.model_validate(option)
            for option in model.get("reasoning_options") or []
            if isinstance(option, dict) and isinstance(option.get("type"), str)
        ],
        tool_call=bool(model.get("tool_call")),
        attachment=bool(model.get("attachment")),
        input_modalities=list(modalities.get("input") or []),
        output_modalities=list(modalities.get("output") or []),
        context_window=limit.get("context"),
        max_output_tokens=limit.get("output"),
        cost=CatalogCost.model_validate(cost) if isinstance(cost, dict) else None,
        status=model.get("status"),
    )


def load_bundled_catalog() -> ModelCatalog:
    with gzip.open(SNAPSHOT_PATH, "rt", encoding="utf-8") as snapshot:
        payload = json.load(snapshot)
    return parse_catalog(payload, fetched_at=None, source="bundled")


@dataclass
class _CatalogCache:
    catalog: ModelCatalog | None = None
    checked_monotonic: float = 0.0


_cache = _CatalogCache()


async def get_model_catalog(session: AsyncSession) -> ModelCatalog:
    """Return the current catalog: the last good refresh, else the snapshot.

    Each process keeps one parsed copy and re-checks the stored row's
    timestamp at most once a minute, reloading only when it changed.
    """
    now = time.monotonic()
    cached = _cache.catalog
    if cached is not None and now - _cache.checked_monotonic < _RELOAD_CHECK_SECONDS:
        return cached

    fetched_at = (
        await session.execute(
            select(AIModelCatalog.fetched_at).where(AIModelCatalog.source == CATALOG_SOURCE)
        )
    ).scalar_one_or_none()
    if cached is None or cached.fetched_at != fetched_at:
        if fetched_at is None:
            cached = load_bundled_catalog()
        else:
            row = await session.get(AIModelCatalog, CATALOG_SOURCE)
            assert row is not None
            cached = parse_catalog(row.payload, fetched_at=row.fetched_at, source="refreshed")
    _cache.catalog = cached
    _cache.checked_monotonic = now
    return cached


def reset_catalog_cache() -> None:
    """Forget the parsed copy so the next read reloads (tests, refresh)."""
    _cache.catalog = None
    _cache.checked_monotonic = 0.0


class CatalogRefreshRejected(Exception):
    """The source answered, but its catalog failed the sanity checks."""


@dataclass(frozen=True)
class CatalogRefreshResult:
    changed: bool
    provider_count: int
    model_count: int
    fetched_at: datetime


async def refresh_model_catalog(
    session: AsyncSession,
    *,
    url: str = MODELS_DEV_URL,
    timeout_seconds: float = 30.0,
) -> CatalogRefreshResult:
    """Fetch the catalog if it changed and store it as the last good copy.

    Raises ``httpx.HTTPError`` when the source is unreachable and
    ``CatalogRefreshRejected`` when it returns an implausible catalog. In
    both cases the stored copy is left untouched.
    """
    now = datetime.now(timezone.utc)
    row = await session.get(AIModelCatalog, CATALOG_SOURCE)
    headers = {"If-None-Match": row.etag} if row and row.etag else {}
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as http:
        response = await http.get(url, headers=headers)
    if response.status_code == 304 and row is not None:
        row.checked_at = now
        await session.flush()
        return CatalogRefreshResult(False, row.provider_count, row.model_count, row.fetched_at)
    response.raise_for_status()

    payload = trim_catalog(response.json())
    provider_count = len(payload)
    model_count = sum(len(p["models"]) for p in payload.values())
    baseline = row.model_count if row else load_bundled_catalog().model_count
    if provider_count < _MIN_PROVIDERS or model_count < baseline * _MIN_MODEL_RATIO:
        raise CatalogRefreshRejected(
            f"models.dev returned {provider_count} providers and {model_count} models; "
            f"keeping the last good catalog ({baseline} models)."
        )

    etag = response.headers.get("etag")
    if row is None:
        row = AIModelCatalog(source=CATALOG_SOURCE)
        session.add(row)
    changed = row.payload != payload
    row.etag = etag
    row.checked_at = now
    if changed:
        row.payload = payload
        row.provider_count = provider_count
        row.model_count = model_count
        row.fetched_at = now
    await session.flush()
    return CatalogRefreshResult(changed, row.provider_count, row.model_count, row.fetched_at)
