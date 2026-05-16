import time
import pytest
from src.services.oauth_state import encode_state, decode_state, OAuthStateError


def test_round_trip_no_mapping():
    token = encode_state({"provider_id": "abc"})
    payload = decode_state(token)
    assert payload["provider_id"] == "abc"
    assert payload.get("mapping_id") is None
    assert "nonce" in payload


def test_round_trip_with_mapping():
    token = encode_state({"provider_id": "abc", "mapping_id": "xyz"})
    payload = decode_state(token)
    assert payload["mapping_id"] == "xyz"


def test_tampered_state_rejected():
    token = encode_state({"provider_id": "abc"})
    # Flip one byte in the body (before the signature)
    body, sig = token.rsplit(".", 1)
    tampered = body[:-1] + ("0" if body[-1] != "0" else "1") + "." + sig
    with pytest.raises(OAuthStateError):
        decode_state(tampered)


def test_expired_state_rejected(monkeypatch):
    token = encode_state({"provider_id": "abc"}, ttl_seconds=1)
    _real_time = time.time
    monkeypatch.setattr("src.services.oauth_state.time.time", lambda: _real_time() + 10)
    with pytest.raises(OAuthStateError):
        decode_state(token)


def test_decode_missing_signature_rejected():
    with pytest.raises(OAuthStateError):
        decode_state("notavalidtoken")
