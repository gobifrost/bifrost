from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.models.contracts.codex_gateway import (
    CodexGatewayKeyContext,
    CodexGatewayRequestContext,
    CodexGatewayUpstreamAccount,
)
from src.services.codex_gateway.policy import (
    CodexGatewayPolicyEngine,
    CodexGatewayPolicyError,
)


def _uuid(value: str) -> UUID:
    return UUID(value)


def _key_for(user_id: UUID, *, allowed_models: list[str] | None = None) -> CodexGatewayKeyContext:
    return CodexGatewayKeyContext(
        id=uuid4(),
        user_id=user_id,
        project_id=uuid4(),
        name="developer workstation",
        allowed_models=allowed_models or ["gpt-5.1-codex"],
        denied_models=[],
        status="active",
    )


def _account_for(user_id: UUID, *, revoked: bool = False) -> CodexGatewayUpstreamAccount:
    return CodexGatewayUpstreamAccount(
        id=uuid4(),
        user_id=user_id,
        upstream_subject=f"chatgpt-user-{user_id}",
        upstream_email=f"{user_id}@example.test",
        upstream_workspace_id="workspace-midtown",
        access_token_expires_at=datetime(2026, 5, 22, 20, 0, tzinfo=timezone.utc),
        revoked_at=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc) if revoked else None,
    )


def test_gateway_key_resolves_only_same_users_upstream_account():
    user_a = _uuid("11111111-1111-4111-8111-111111111111")
    user_b = _uuid("22222222-2222-4222-8222-222222222222")
    key = _key_for(user_a)
    engine = CodexGatewayPolicyEngine()

    resolved = engine.resolve_upstream_account(
        key,
        [
            _account_for(user_b),
            _account_for(user_a),
        ],
    )

    assert resolved.user_id == user_a


def test_gateway_key_fails_closed_instead_of_using_another_users_account():
    user_a = _uuid("11111111-1111-4111-8111-111111111111")
    user_b = _uuid("22222222-2222-4222-8222-222222222222")
    key = _key_for(user_a)
    engine = CodexGatewayPolicyEngine()

    try:
        engine.resolve_upstream_account(key, [_account_for(user_b)])
    except CodexGatewayPolicyError as exc:
        assert exc.code == "upstream_identity_not_connected"
        assert exc.status_code == 403
    else:
        raise AssertionError("expected cross-user upstream lookup to fail closed")


def test_revoked_upstream_account_is_not_usable():
    user_id = _uuid("11111111-1111-4111-8111-111111111111")
    key = _key_for(user_id)
    engine = CodexGatewayPolicyEngine()

    try:
        engine.resolve_upstream_account(key, [_account_for(user_id, revoked=True)])
    except CodexGatewayPolicyError as exc:
        assert exc.code == "upstream_identity_revoked"
    else:
        raise AssertionError("expected revoked upstream account to fail closed")


def test_multiple_active_upstream_accounts_fail_closed():
    user_id = _uuid("11111111-1111-4111-8111-111111111111")
    key = _key_for(user_id)
    engine = CodexGatewayPolicyEngine()

    try:
        engine.resolve_upstream_account(
            key,
            [
                _account_for(user_id),
                _account_for(user_id),
            ],
        )
    except CodexGatewayPolicyError as exc:
        assert exc.code == "upstream_identity_ambiguous"
        assert exc.status_code == 403
    else:
        raise AssertionError("expected ambiguous upstream account lookup to fail closed")


def test_model_allowlist_denial_is_structured_and_auditable():
    user_id = _uuid("11111111-1111-4111-8111-111111111111")
    key = _key_for(user_id, allowed_models=["gpt-5.1-codex"])
    account = _account_for(user_id)
    request = CodexGatewayRequestContext(
        request_id="req_bifrost_123",
        endpoint="/v1/responses",
        model="gpt-5.1",
        streaming=False,
        client_type="openai-compatible",
    )
    engine = CodexGatewayPolicyEngine()

    decision = engine.evaluate(key, account, request)

    assert decision.allowed is False
    assert decision.code == "model_not_allowed"
    assert decision.audit_metadata["policy_decision"] == "deny"
    assert decision.openai_error.type == "invalid_request_error"


def test_visible_models_are_filtered_by_allowlist_and_denylist():
    user_id = _uuid("11111111-1111-4111-8111-111111111111")
    key = _key_for(user_id, allowed_models=["gpt-5.1-codex", "gpt-5.1"])
    key.denied_models = ["gpt-5.1"]
    engine = CodexGatewayPolicyEngine()

    visible = engine.filter_visible_models(
        key,
        [
            "gpt-5.1-codex",
            "gpt-5.1",
            "gpt-4.1",
        ],
    )

    assert visible == ["gpt-5.1-codex"]


def test_metadata_logging_excludes_prompt_and_response_by_default():
    user_id = _uuid("11111111-1111-4111-8111-111111111111")
    key = _key_for(user_id)
    account = _account_for(user_id)
    request = CodexGatewayRequestContext(
        request_id="req_bifrost_123",
        endpoint="/v1/responses",
        model="gpt-5.1-codex",
        streaming=True,
        client_type="codex-cli",
        source_ip="192.0.2.10",
        client_user_agent="codex-cli/1.0",
        sensitive_input_preview="do not log this prompt",
        sensitive_output_preview="do not log this response",
    )
    engine = CodexGatewayPolicyEngine()

    decision = engine.evaluate(key, account, request)
    metadata = decision.audit_metadata

    assert decision.allowed is True
    assert metadata["request_id"] == "req_bifrost_123"
    assert metadata["upstream_email"] == account.upstream_email
    assert metadata["streaming"] is True
    assert "do not log this prompt" not in str(metadata)
    assert "do not log this response" not in str(metadata)
