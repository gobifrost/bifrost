"""Builder turn result projection tests."""

from src.models.contracts.sandbox_runner import SandboxBuilderTurnCompletion
from src.services.builder.agent_turns import _builder_usage_result


def test_builder_usage_result_uses_shared_runtime_units() -> None:
    assert _builder_usage_result(
        model_request_count=12,
        token_count_input=230_000,
        token_count_output=20_000,
        max_requests=80,
        max_tokens=2_000_000,
    ) == {
        "llm_usage": {
            "calls": 12,
            "input_tokens": 230_000,
            "output_tokens": 20_000,
        },
        "llm_limits": {
            "max_calls": 80,
            "max_tokens": 2_000_000,
        },
    }


def test_remote_completion_carries_model_request_count() -> None:
    completion = SandboxBuilderTurnCompletion(
        status="succeeded",
        output_sha256="a" * 64,
        final_text="Finished",
        model_request_count=7,
    )

    assert completion.model_request_count == 7
