import base64
import json

from src.services.oauth_entity_id import extract_entity_id


def _make_id_token(claims: dict) -> str:
    """Build an unsigned JWT-like string with the given claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}."


def test_returns_none_when_no_config():
    assert extract_entity_id(None, callback_url_params={}, token_response={}) is None


def test_url_param_extraction():
    source = {"type": "url_param", "key": "realmId"}
    result = extract_entity_id(source, callback_url_params={"realmId": "12345"}, token_response={})
    assert result == "12345"


def test_url_param_missing_returns_none():
    source = {"type": "url_param", "key": "realmId"}
    result = extract_entity_id(source, callback_url_params={}, token_response={})
    assert result is None


def test_token_response_field_extraction():
    source = {"type": "token_response_field", "key": "stripe_user_id"}
    result = extract_entity_id(source, callback_url_params={}, token_response={"stripe_user_id": "acct_1"})
    assert result == "acct_1"


def test_token_response_dotted_path():
    source = {"type": "token_response_field", "key": "team.id"}
    result = extract_entity_id(
        source, callback_url_params={}, token_response={"team": {"id": "T123"}}
    )
    assert result == "T123"


def test_id_token_claim_extraction():
    source = {"type": "id_token_claim", "key": "tid"}
    id_token = _make_id_token({"tid": "tenant-uuid", "sub": "user"})
    result = extract_entity_id(
        source, callback_url_params={}, token_response={"id_token": id_token}
    )
    assert result == "tenant-uuid"


def test_id_token_claim_missing_id_token_returns_none():
    source = {"type": "id_token_claim", "key": "tid"}
    result = extract_entity_id(source, callback_url_params={}, token_response={})
    assert result is None


def test_unknown_type_returns_none():
    source = {"type": "future_source", "key": "x"}
    assert extract_entity_id(source, callback_url_params={}, token_response={}) is None
