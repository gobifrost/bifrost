"""External App identity and byte-exact ingress verification contracts."""

from datetime import datetime, timezone
import hashlib
import hmac
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from shared.github_app_auth import GitHubAppError, issue_app_jwt, parse_verified_webhook, validate_private_key
from shared.github_app_client import retry_delay
from shared.models import GitHubAppConfigure


@pytest.fixture(scope="module")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return key, pem


def test_github_jwt_has_only_external_app_authority(rsa_key):
    key, pem = rsa_key
    now = datetime.now(timezone.utc)
    token = issue_app_jwt(123, pem, now=now)
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], issuer="123")
    assert claims == {"iss": "123", "iat": int(now.timestamp()) - 60, "exp": int(now.timestamp()) + 540}
    with pytest.raises(jwt.InvalidAlgorithmError):
        jwt.decode(token, "platform-key", algorithms=["HS256"])


def test_validated_rsa_key_and_request_are_write_only(rsa_key):
    _, pem = rsa_key
    validate_private_key(pem)
    body = GitHubAppConfigure(app_id=123, private_key=pem, webhook_secret="s" * 32)
    assert pem not in repr(body)
    assert "s" * 32 not in body.model_dump_json()


@pytest.mark.parametrize("pem", ["invalid", "", "-----BEGIN PUBLIC KEY-----\ninvalid\n-----END PUBLIC KEY-----"])
def test_bad_keys_have_sanitized_errors(pem):
    with pytest.raises(GitHubAppError, match="Enter an unencrypted RSA private key"):
        validate_private_key(pem)


def signed(body, secret="s" * 32):
    return {"x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
            "x-github-event": "issues", "x-github-delivery": "delivery-1"}


def test_original_bytes_are_required_and_identity_is_preserved():
    body = b'{ "action": "opened", "title": "caf\\u00e9" }'
    assert parse_verified_webhook(body, signed(body), "s" * 32) == ("delivery-1", "issues", json.loads(body))
    with pytest.raises(GitHubAppError, match="signature"):
        parse_verified_webhook(json.dumps(json.loads(body)).encode(), signed(body), "s" * 32)


@pytest.mark.parametrize("signature", [None, "", "sha1=abc", "sha256=" + "0" * 64, "SHA256=" + "a" * 64])
def test_missing_or_bad_signatures_are_rejected(signature):
    body = b'{}'
    headers = signed(body)
    if signature is None:
        del headers["x-hub-signature-256"]
    else:
        headers["x-hub-signature-256"] = signature
    with pytest.raises(GitHubAppError) as error:
        parse_verified_webhook(body, headers, "s" * 32)
    assert error.value.status_code == 401


@pytest.mark.parametrize("body", [b'[]', b'invalid', b'null', b'1'])
def test_signed_non_objects_are_rejected(body):
    with pytest.raises(GitHubAppError, match="JSON|object"):
        parse_verified_webhook(body, signed(body), "s" * 32)


def test_unsigned_configuration_and_missing_delivery_identity_fail_closed():
    with pytest.raises(GitHubAppError, match="not configured"):
        parse_verified_webhook(b'{}', {}, "")
    headers = signed(b'{}')
    del headers["x-github-delivery"]
    with pytest.raises(GitHubAppError, match="identity"):
        parse_verified_webhook(b'{}', headers, "s" * 32)


def test_quota_delay_obeys_provider_and_handles_malformed_headers():
    assert retry_delay(httpx.Headers({"retry-after": "120"}), now=0) == 120
    assert retry_delay(httpx.Headers({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "150"}), now=20) == 130
    assert retry_delay(httpx.Headers({"retry-after": "NaN"}), now=0) == 60
    assert retry_delay(httpx.Headers({"retry-after": "999999"}), now=0) == 86400
