"""Engine tokens must outlive the execution they are minted for."""

from datetime import datetime, timedelta, timezone

from src.core.security import NO_TIMEOUT_TOKEN_SECONDS, decode_token, mint_engine_token


def _expiry(timeout_seconds: int) -> datetime:
    token, expires_at = mint_engine_token(
        execution_id="00000000-0000-0000-0000-000000000123",
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=timeout_seconds,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["engine_execution_id"] == "00000000-0000-0000-0000-000000000123"
    return datetime.fromisoformat(expires_at)


def test_token_covers_timeout_plus_flush_margin():
    remaining = _expiry(600) - datetime.now(timezone.utc)
    assert timedelta(seconds=890) < remaining <= timedelta(seconds=900)


def test_no_timeout_workflow_gets_a_day_not_five_minutes():
    remaining = _expiry(0) - datetime.now(timezone.utc)
    assert remaining > timedelta(seconds=NO_TIMEOUT_TOKEN_SECONDS - 10)
