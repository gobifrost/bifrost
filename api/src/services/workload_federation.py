"""Generic RFC 8693 inbound JWT federation for Bifrost identities.

Only configured credential issuers are discovered. Assertion material is
never logged, persisted, or returned in an error response.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.models.contracts.identities import SUPPORTED_SUBJECT_TOKEN_TYPES
from src.models.orm.federated_credentials import FederatedCredential
from src.services.federated_credentials import exact_claims_match
from src.services.oidc_rp import IDTokenValidationError, OIDCRelyingParty
from shared.url_safety import validate_public_https_url

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ISSUED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
MAX_SUBJECT_TOKEN_CHARS = 32_768
_CACHE_SECONDS = 300.0
_ASYMMETRIC_JWS_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)


class WorkloadExchangeError(Exception):
    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class _VerificationProfile:
    issuer: str
    audience: str
    discovery_url: str


@dataclass
class _CachedVerifier:
    verifier: OIDCRelyingParty
    expires_at: float


_verifiers: dict[_VerificationProfile, _CachedVerifier] = {}
_verifier_lock = asyncio.Lock()


def _default_discovery_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


def _profile(credential: FederatedCredential) -> _VerificationProfile:
    return _VerificationProfile(
        issuer=credential.issuer,
        audience=credential.audience,
        discovery_url=credential.discovery_url or _default_discovery_url(credential.issuer),
    )


def _validate_public_https_url(url: str) -> None:
    """Use the platform's existing public-URL SSRF guard before every fetch."""
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS URLs are allowed")
    validate_public_https_url(url)


async def _fetch_metadata(profile: _VerificationProfile) -> dict[str, Any]:
    try:
        await asyncio.to_thread(_validate_public_https_url, profile.discovery_url)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(profile.discovery_url)
            response.raise_for_status()
            metadata = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.") from exc
    if not isinstance(metadata, dict) or metadata.get("issuer") != profile.issuer:
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.")
    jwks_uri = metadata.get("jwks_uri")
    if not isinstance(jwks_uri, str):
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.")
    try:
        await asyncio.to_thread(_validate_public_https_url, jwks_uri)
    except ValueError as exc:
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.") from exc
    advertised = metadata.get("id_token_signing_alg_values_supported", ["RS256"])
    if not isinstance(advertised, list):
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.")
    allowed = [algorithm for algorithm in advertised if isinstance(algorithm, str) and algorithm in _ASYMMETRIC_JWS_ALGORITHMS]
    if not allowed:
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.")
    return {**metadata, "id_token_signing_alg_values_supported": allowed}


def _guarded_jwks_provider(jwks_uri: str):
    keys: dict[str, Any] | None = None
    lock = asyncio.Lock()

    async def provider(force: bool) -> dict[str, Any]:
        nonlocal keys
        async with lock:
            if keys is not None and not force:
                return keys
            try:
                await asyncio.to_thread(_validate_public_https_url, jwks_uri)
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                    response = await client.get(jwks_uri)
                    response.raise_for_status()
                    body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise IDTokenValidationError("Signing keys are unavailable") from exc
            if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
                raise IDTokenValidationError("Signing keys are unavailable")
            keys = body
            return keys

    return provider


async def _verifier(profile: _VerificationProfile) -> OIDCRelyingParty:
    """Return one bounded shared Authlib verifier per configured profile."""
    async with _verifier_lock:
        expired = [item for item, value in _verifiers.items() if value.expires_at <= time.monotonic()]
        for item in expired:
            await _verifiers.pop(item).verifier.aclose()
        cached = _verifiers.get(profile)
        if cached is not None and cached.expires_at > time.monotonic():
            return cached.verifier
        if cached is not None:
            await cached.verifier.aclose()
        metadata = await _fetch_metadata(profile)
        verifier = OIDCRelyingParty(
            client_id=profile.audience,
            server_metadata=metadata,
            claims_options={
                "iss": {"essential": True, "value": profile.issuer},
                "aud": {"essential": True, "values": [profile.audience]},
                "sub": {"essential": True},
                "exp": {"essential": True},
                "iat": {"essential": True},
            },
            jwks_provider=_guarded_jwks_provider(metadata["jwks_uri"]),
            follow_redirects=False,
        )
        _verifiers[profile] = _CachedVerifier(verifier, time.monotonic() + _CACHE_SECONDS)
        return verifier


async def verify_profile_assertion(profile: _VerificationProfile, subject_token: str) -> dict[str, Any]:
    """Verify a configured profile through Authlib, including unknown-kid refresh."""
    try:
        return await (await _verifier(profile)).parse_verified_workload_token(subject_token)
    except IDTokenValidationError as exc:
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.") from exc


def _routing_hints(subject_token: str) -> tuple[str | None, set[str] | None]:
    """Read unsigned iss/aud solely to avoid needless configured-profile I/O."""
    try:
        payload_segment = subject_token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4))
        payload = json.loads(raw)
    except (IndexError, UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    issuer = payload.get("iss") if isinstance(payload.get("iss"), str) else None
    audience = payload.get("aud")
    if isinstance(audience, str):
        return issuer, {audience}
    if isinstance(audience, list) and all(isinstance(item, str) for item in audience):
        return issuer, set(audience)
    return issuer, None


def unverified_assertion_hints(subject_token: str) -> dict[str, str | list[str]]:
    """Return bounded unsigned routing hints for a failed-exchange audit.

    These are deliberately labeled as unverified. They are never used for
    acceptance and never include the assertion itself.
    """
    try:
        payload_segment = subject_token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4))
        payload = json.loads(raw)
    except (IndexError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}

    hints: dict[str, str | list[str]] = {}
    for source, target, limit in (
        ("iss", "unverified_issuer", 2048),
        ("sub", "unverified_subject", 1024),
    ):
        value = payload.get(source)
        if isinstance(value, str) and value:
            hints[target] = value[:limit]
    audience = payload.get("aud")
    if isinstance(audience, str) and audience:
        hints["unverified_audience"] = audience[:512]
    elif isinstance(audience, list) and all(isinstance(item, str) for item in audience):
        hints["unverified_audience"] = [item[:512] for item in audience[:32]]
    return hints


async def matching_credential(
    credentials: list[FederatedCredential], subject_token: str
) -> tuple[FederatedCredential, dict[str, Any]] | None:
    """Return one verified exact match, never accepting routing hints as proof."""
    hint_issuer, hint_audiences = _routing_hints(subject_token)
    if hint_issuer is None or hint_audiences is None:
        return None
    candidates = [
        credential
        for credential in credentials
        if (hint_issuer is None or credential.issuer == hint_issuer)
        and (hint_audiences is None or credential.audience in hint_audiences)
    ]
    groups: dict[_VerificationProfile, list[FederatedCredential]] = {}
    for credential in candidates:
        groups.setdefault(_profile(credential), []).append(credential)
    matched: list[tuple[FederatedCredential, dict[str, Any]]] = []
    for profile, group in groups.items():
        try:
            claims = await verify_profile_assertion(profile, subject_token)
        except WorkloadExchangeError:
            continue
        matched.extend((credential, claims) for credential in group if exact_claims_match(credential, claims))
    return matched[0] if len(matched) == 1 else None


def validate_exchange_request(form: Any) -> tuple[str, str]:
    grant_type = form.get("grant_type")
    if grant_type != TOKEN_EXCHANGE_GRANT:
        raise WorkloadExchangeError("unsupported_grant_type", "Unsupported grant type.")
    subject_token = form.get("subject_token")
    subject_token_type = form.get("subject_token_type")
    if not isinstance(subject_token, str) or not subject_token or not isinstance(subject_token_type, str):
        raise WorkloadExchangeError("invalid_request", "subject_token and subject_token_type are required.")
    if subject_token_type not in SUPPORTED_SUBJECT_TOKEN_TYPES:
        raise WorkloadExchangeError("invalid_request", "Unsupported subject_token_type.")
    if len(subject_token) > MAX_SUBJECT_TOKEN_CHARS:
        raise WorkloadExchangeError("invalid_grant", "The subject token could not be exchanged.")
    if form.get("actor_token") or form.get("actor_token_type"):
        raise WorkloadExchangeError("invalid_request", "Delegation is not supported.")
    if form.get("resource") or form.get("audience"):
        raise WorkloadExchangeError("invalid_target", "Explicit token targets are not supported.")
    requested_token_type = form.get("requested_token_type")
    if requested_token_type not in (None, "", ISSUED_TOKEN_TYPE):
        raise WorkloadExchangeError("invalid_target", "requested_token_type must be an access token.")
    if form.get("scope") not in (None, ""):
        raise WorkloadExchangeError("invalid_scope", "Scopes are not supported for workload token exchange.")
    return subject_token, subject_token_type
