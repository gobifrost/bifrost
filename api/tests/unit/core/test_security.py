"""Typed durable runtime-token minting."""

from uuid import uuid4

from src.core.security import decode_token, mint_engine_token


def test_workflow_runtime_token_identifies_only_its_execution() -> None:
    execution_id = str(uuid4())
    token, _ = mint_engine_token(execution_id=execution_id, timeout_seconds=300)

    payload = decode_token(token, expected_type="access")

    assert payload is not None
    assert payload["sub"] == execution_id
    assert payload["credential_kind"] == "workflow_execution"
    for claim in (
        "email",
        "is_superuser",
        "engine_execution_id",
        "engine_solution_id",
        "engine_caller_user_id",
        "engine_run_user_id",
    ):
        assert claim not in payload
