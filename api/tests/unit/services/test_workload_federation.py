"""Focused contracts for generic inbound workload federation."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.models.contracts.identities import (
    FederatedCredentialCreate,
    FederatedCredentialUpdate,
    SUBJECT_TOKEN_ACCESS_TOKEN,
)
from src.services.federated_credentials import exact_claims_match
from src.services import workload_federation
from src.services.workload_federation import (
    _default_discovery_url,
    matching_credential,
    unverified_assertion_hints,
    validate_exchange_request,
)


def test_federation_url_guard_is_independent_of_sdk_generator_host_policy(monkeypatch) -> None:
    monkeypatch.setenv("SDK_GENERATOR_ALLOWED_HOSTS", "sdk-only.example")
    monkeypatch.setattr("shared.url_safety.socket.getaddrinfo", lambda *_: [(2, 1, 6, "", ("1.1.1.1", 443))])
    workload_federation._validate_public_https_url("https://token.actions.githubusercontent.com")
    monkeypatch.setattr("shared.url_safety.socket.getaddrinfo", lambda *_: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="non-public"):
        workload_federation._validate_public_https_url("https://issuer.example")


def test_azure_v1_issuer_trailing_slash_is_preserved_for_exact_metadata_match() -> None:
    issuer = "https://sts.windows.net/00000000-0000-0000-0000-000000000001/"
    credential = FederatedCredentialCreate(name="Azure", issuer=issuer, audience="api://resource", subject="workload")

    assert credential.issuer == issuer
    assert _default_discovery_url(credential.issuer) == issuer.rstrip("/") + "/.well-known/openid-configuration"


@pytest.mark.parametrize("field", ["name", "issuer", "audience", "subject", "claims", "subject_token_type", "token_ttl_seconds", "enabled"])
def test_nullable_update_fields_reject_null_except_discovery_url(field: str) -> None:
    with pytest.raises(ValidationError):
        FederatedCredentialUpdate(**{field: None})
    assert FederatedCredentialUpdate(discovery_url=None).discovery_url is None


def test_generic_immutable_and_unicode_exact_claims_are_accepted_without_provider_parsing() -> None:
    row = SimpleNamespace(
        subject="repo:jackmusick@6733569/bifrost-git-acceptance@1399040965:ref:refs/heads/main",
        claims={"repository_id": "1399040965", "label": "日本語"},
    )

    assert exact_claims_match(row, {"sub": row.subject, "repository_id": "1399040965", "label": "日本語"})
    assert not exact_claims_match(row, {"sub": row.subject, "repository_id": "other", "label": "日本語"})


def test_exchange_requires_configured_rfc_subject_token_type() -> None:
    token, token_type = validate_exchange_request(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": "assertion",
            "subject_token_type": SUBJECT_TOKEN_ACCESS_TOKEN,
        }
    )

    assert (token, token_type) == ("assertion", SUBJECT_TOKEN_ACCESS_TOKEN)


@pytest.mark.parametrize("parameter", ["resource", "audience", "actor_token_type"])
def test_exchange_rejects_unsupported_targets_and_delegation(parameter: str) -> None:
    with pytest.raises(workload_federation.WorkloadExchangeError):
        validate_exchange_request({
            "grant_type": workload_federation.TOKEN_EXCHANGE_GRANT,
            "subject_token": "assertion",
            "subject_token_type": SUBJECT_TOKEN_ACCESS_TOKEN,
            parameter: "unsupported",
        })


@pytest.mark.asyncio
async def test_overlapping_profiles_are_denied_not_first_match(monkeypatch) -> None:
    first = SimpleNamespace(issuer="https://issuer.example", audience="aud", discovery_url=None, subject="sub", claims={})
    second = SimpleNamespace(issuer="https://issuer.example", audience="aud", discovery_url=None, subject="sub", claims={})

    async def verified(_profile, _token):
        return {"iss": "https://issuer.example", "aud": "aud", "sub": "sub", "exp": 2_000_000_000, "iat": 1}

    monkeypatch.setattr(workload_federation, "verify_profile_assertion", verified)
    token = "h." + __import__("base64").urlsafe_b64encode(b'{"iss":"https://issuer.example","aud":"aud"}').rstrip(b"=").decode() + ".s"

    assert await matching_credential([first, second], token) is None


def test_failed_exchange_audit_hints_are_explicitly_unverified_and_bounded() -> None:
    payload = __import__("base64").urlsafe_b64encode(
        b'{"iss":"https://issuer.example","aud":["bifrost"],"sub":"workload"}'
    ).rstrip(b"=").decode()

    assert unverified_assertion_hints(f"h.{payload}.s") == {
        "unverified_issuer": "https://issuer.example",
        "unverified_audience": ["bifrost"],
        "unverified_subject": "workload",
    }
