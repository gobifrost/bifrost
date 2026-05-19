"""Unit tests for entity_id picker candidate enumeration + secret scrubbing."""

import base64
import json

from src.services.oauth_entity_id import enumerate_candidate_fields


def _id_token(claims: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}."


def test_returns_empty_when_only_protocol_fields():
    """Pure OAuth response (access_token, refresh_token, expires_in, etc.) yields no candidates."""
    out = enumerate_candidate_fields(
        callback_url_params={},
        token_response={
            "access_token": "atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
            "scope": "read write",
            "token_type": "Bearer",
        },
    )
    assert out == []


def test_returns_url_param_candidates():
    out = enumerate_candidate_fields(
        callback_url_params={"realmId": "12345", "code": "abc", "state": "xyz"},
        token_response={},
    )
    paths = {(c["type"], c["key"]) for c in out}
    assert ("url_param", "realmId") in paths
    assert ("url_param", "code") not in paths
    assert ("url_param", "state") not in paths


def test_returns_token_response_candidates_with_dotted_paths():
    out = enumerate_candidate_fields(
        callback_url_params={},
        token_response={
            "access_token": "atk",
            "team": {"id": "T123", "name": "Acme"},
            "stripe_user_id": "acct_1",
        },
    )
    paths = {(c["type"], c["key"]): c["value"] for c in out}
    assert paths.get(("token_response_field", "team.id")) == "T123"
    assert paths.get(("token_response_field", "team.name")) == "Acme"
    assert paths.get(("token_response_field", "stripe_user_id")) == "acct_1"
    assert ("token_response_field", "access_token") not in paths


def test_returns_id_token_claim_candidates():
    out = enumerate_candidate_fields(
        callback_url_params={},
        token_response={
            "access_token": "atk",
            "id_token": _id_token({"tid": "tenant-uuid", "sub": "user-id", "iss": "https://example.com"}),
        },
    )
    paths = {(c["type"], c["key"]): c["value"] for c in out}
    assert paths.get(("id_token_claim", "tid")) == "tenant-uuid"
    assert paths.get(("id_token_claim", "sub")) == "user-id"
    assert paths.get(("id_token_claim", "iss")) == "https://example.com"


def test_scrubs_token_suffix_patterns():
    out = enumerate_candidate_fields(
        callback_url_params={},
        token_response={
            "id": "abc",
            "session_token": "should-hide",
            "api_key": "should-hide",
            "client_secret": "should-hide",
            "hmac_signature": "should-hide",
        },
    )
    keys = {(c["type"], c["key"]) for c in out}
    assert ("token_response_field", "id") in keys
    assert ("token_response_field", "session_token") not in keys
    assert ("token_response_field", "api_key") not in keys
    assert ("token_response_field", "client_secret") not in keys
    assert ("token_response_field", "hmac_signature") not in keys


def test_scrubs_case_insensitively():
    out = enumerate_candidate_fields(
        callback_url_params={"AccessToken": "x", "RealmID": "y"},
        token_response={},
    )
    keys = {(c["type"], c["key"]) for c in out}
    assert ("url_param", "AccessToken") not in keys
    assert ("url_param", "RealmID") in keys


def test_coerces_non_string_values_to_strings():
    out = enumerate_candidate_fields(
        callback_url_params={},
        token_response={"count": 42, "active": True, "id": None},
    )
    paths = {(c["type"], c["key"]): c["value"] for c in out}
    assert ("token_response_field", "id") not in paths
    assert paths.get(("token_response_field", "count")) == "42"
    assert paths.get(("token_response_field", "active")) == "True"
