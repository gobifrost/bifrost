"""Resolution order for profile-level default max output tokens.

Precedence (see ``request_max_tokens``):
explicit agent override > profile default > Anthropic required / DeepSeek
guard > provider default (None). There are no generic per-kind fallbacks.
"""

import importlib.util
from pathlib import Path

import httpx2
import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from src.services.agent_runtime.model_factory import (
    agent_model_settings,
    create_agent_model,
)
from src.services.ai_model_service import AIModelService
from src.services.llm.base import (
    DEEPSEEK_FAMILY_MAX_TOKENS,
    LLMConfig,
    is_deepseek_family,
    request_max_tokens,
)


def _config(
    provider: str = "openai",
    model: str = "gpt-5",
    endpoint: str | None = None,
    default_max_tokens: int | None = None,
) -> LLMConfig:
    return LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        api_key="test-key",
        endpoint=endpoint,
        default_max_tokens=default_max_tokens,
    )


# ==================== precedence ====================


def test_agent_override_beats_profile_beats_fallback() -> None:
    config = _config(default_max_tokens=9_000)
    assert (
        request_max_tokens(config, 4_000, agent_kind="worker") == 4_000
    )
    assert (
        request_max_tokens(config, None, agent_kind="worker") == 9_000
    )


@pytest.mark.parametrize(
    "kind", [None, "worker", "coding", "triage", "dispatch", "small_task", "unknown-kind"]
)
def test_no_generic_kind_fallback_when_both_null(kind: str | None) -> None:
    """Without agent/profile values (and not Anthropic/DeepSeek), kinds
    resolve to the provider default (None) — no 8000/32000 generic caps."""
    assert request_max_tokens(_config(), None, agent_kind=kind) is None


def test_undeclared_kind_falls_through_to_provider_default() -> None:
    assert request_max_tokens(_config(), None) is None
    assert request_max_tokens(_config(), None, agent_kind="unknown-kind") is None


# ==================== Anthropic unchanged ====================


def test_anthropic_stays_16384_unless_agent_or_profile_sets_value() -> None:
    anthropic = _config(provider="anthropic", model="claude-sonnet")
    assert request_max_tokens(anthropic, None) == 16_384
    assert request_max_tokens(anthropic, None, agent_kind="worker") == 16_384
    assert request_max_tokens(anthropic, None, agent_kind="triage") == 16_384
    assert (
        request_max_tokens(_config(provider="anthropic", model="x", default_max_tokens=5_000), None)
        == 5_000
    )
    assert request_max_tokens(anthropic, 2_000) == 2_000


# ==================== DeepSeek family guard ====================


@pytest.mark.parametrize(
    ("model", "endpoint"),
    [
        ("deepseek/deepseek-chat", "https://openrouter.ai/api/v1"),
        ("~deepseek/deepseek-v4-flash-latest", "https://openrouter.ai/api/v1"),
        ("deepseek-chat", "https://api.deepseek.com/v1"),
        ("deepseek-reasoner", "https://api.deepseek.com/v1"),
        ("deepseek-r1", "https://models.example.test/v1"),
        ("gpt-5", "https://deepseek.example.test/v1"),
    ],
)
def test_deepseek_family_detection(model: str, endpoint: str) -> None:
    assert is_deepseek_family(_config(model=model, endpoint=endpoint)) is True


@pytest.mark.parametrize(
    ("model", "endpoint"),
    [
        ("gpt-5", None),
        ("gpt-5", "https://api.openai.com/v1"),
        ("claude-sonnet", None),
        ("openai/gpt-5", "https://openrouter.ai/api/v1"),
    ],
)
def test_non_deepseek_models_not_flagged(model: str, endpoint: str | None) -> None:
    assert is_deepseek_family(_config(model=model, endpoint=endpoint)) is False


@pytest.mark.parametrize("kind", [None, "worker", "coding", "triage"])
def test_deepseek_guard_fires_only_when_both_null(kind: str | None) -> None:
    config = _config(model="deepseek-chat", endpoint="https://api.deepseek.com/v1")
    assert request_max_tokens(config, None, agent_kind=kind) == 8_000
    assert request_max_tokens(config, None, agent_kind=kind) == DEEPSEEK_FAMILY_MAX_TOKENS
    # Explicit values are untouched by the guard.
    assert request_max_tokens(config, 100, agent_kind=kind) == 100
    assert (
        request_max_tokens(
            _config(
                model="deepseek-chat",
                endpoint="https://api.deepseek.com/v1",
                default_max_tokens=16_000,
            ),
            None,
            agent_kind=kind,
        )
        == 16_000
    )


# ==================== agent_model_settings threading ====================


def test_agent_model_settings_prefers_profile_default_over_provider_default() -> None:
    config = _config(default_max_tokens=12_000)
    settings = agent_model_settings(
        config, max_tokens=None, session_id="run-1", agent_kind="worker"
    )
    assert settings["max_tokens"] == 12_000


def test_agent_model_settings_no_generic_fallback_without_profile_default() -> None:
    settings = agent_model_settings(
        _config(), max_tokens=None, session_id="run-1", agent_kind="worker"
    )
    assert "max_tokens" not in settings


def test_agent_model_settings_explicit_override_wins() -> None:
    config = _config(default_max_tokens=12_000)
    settings = agent_model_settings(
        config, max_tokens=4_000, session_id="run-1", agent_kind="worker"
    )
    assert settings["max_tokens"] == 4_000


# ==================== DeepSeek direct model + wire field ====================


def test_deepseek_direct_forces_chat_adapter_with_plain_max_tokens_profile() -> None:
    from pydantic_ai.models.openai import OpenAIChatModel

    config = _config(model="deepseek-chat", endpoint="https://api.deepseek.com/v1")
    model = create_agent_model(config)
    assert isinstance(model, OpenAIChatModel)
    assert model.profile.get("openai_chat_supports_max_completion_tokens") is False


@pytest.mark.asyncio
async def test_deepseek_direct_sends_plain_max_tokens_on_the_wire() -> None:
    """Resolved cap must land as ``max_tokens``, not ``max_completion_tokens``.

    DeepSeek rejects ``max_completion_tokens``; Pydantic AI maps the generic
    ``max_tokens`` setting onto that field for OpenAI-family paths unless the
    model profile opts out. This test drives a real request through a mocked
    HTTP transport and inspects the JSON body on the wire.
    """
    seen_bodies: list[dict] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        import json

        seen_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1_700_000_000,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "hi"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            },
        )

    config = _config(model="deepseek-chat", endpoint="https://api.deepseek.com/v1")
    # Both agent and profile unset: the DeepSeek family guard resolves 8000.
    settings = agent_model_settings(
        config, max_tokens=None, session_id="run-1", agent_kind="worker"
    )
    assert settings["max_tokens"] == 8_000

    from unittest.mock import patch

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    try:
        with patch(
            "src.services.agent_runtime.model_factory.get_ai_retry_http_client",
            return_value=http_client,
        ):
            model = create_agent_model(config)
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart(content="hello")])],
                {"max_tokens": settings["max_tokens"]},  # type: ignore[typeddict-item]
                ModelRequestParameters(),
            )
    finally:
        await http_client.aclose()

    assert response.text == "hi"
    assert len(seen_bodies) == 1
    body = seen_bodies[0]
    assert body["max_tokens"] == 8_000
    assert "max_completion_tokens" not in body


# ==================== service validation ====================


def test_service_rejects_out_of_range_profile_default() -> None:
    with pytest.raises(ValueError):
        AIModelService._validate_default_max_tokens(0)
    with pytest.raises(ValueError):
        AIModelService._validate_default_max_tokens(200_001)
    AIModelService._validate_default_max_tokens(None)
    AIModelService._validate_default_max_tokens(1)
    AIModelService._validate_default_max_tokens(200_000)


def test_profile_contracts_validate_range() -> None:
    from uuid import uuid4

    from pydantic import ValidationError

    from src.models.contracts.ai_models import (
        AIModelProfileCreate,
        AIModelProfileUpdate,
    )

    with pytest.raises(ValidationError):
        AIModelProfileCreate(
            name="p", connection_id=uuid4(), model="m", default_max_tokens=0
        )
    with pytest.raises(ValidationError):
        AIModelProfileUpdate(default_max_tokens=200_001)
    assert (
        AIModelProfileCreate(
            name="p",
            connection_id=uuid4(),
            model="m",
            default_max_tokens=32_000,
        ).default_max_tokens
        == 32_000
    )


# ==================== migration up/down ====================


MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "20260917_profile_max_tokens.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "_profile_default_max_tokens_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _OpStub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_column(self, table: str, column) -> None:
        self.calls.append(("add_column", table, column.name))

    def create_check_constraint(self, name: str, table: str, condition) -> None:
        self.calls.append(("create_check_constraint", name, table))

    def drop_constraint(self, name: str, table: str, type_: str | None = None) -> None:
        self.calls.append(("drop_constraint", name, table, type_))

    def drop_column(self, table: str, column: str) -> None:
        self.calls.append(("drop_column", table, column))


def test_migration_up_adds_column_and_range_check(monkeypatch) -> None:
    migration = _load_migration()
    stub = _OpStub()
    monkeypatch.setattr(migration, "op", stub)
    migration.upgrade()
    assert ("add_column", "ai_model_profiles", "default_max_tokens") in stub.calls
    assert (
        "create_check_constraint",
        "ck_ai_model_profiles_default_max_tokens_range",
        "ai_model_profiles",
    ) in stub.calls


def test_migration_downgrade_removes_check_and_column(monkeypatch) -> None:
    migration = _load_migration()
    stub = _OpStub()
    monkeypatch.setattr(migration, "op", stub)
    migration.downgrade()
    assert (
        "drop_constraint",
        "ck_ai_model_profiles_default_max_tokens_range",
        "ai_model_profiles",
        "check",
    ) in stub.calls
    assert ("drop_column", "ai_model_profiles", "default_max_tokens") in stub.calls
