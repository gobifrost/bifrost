"""OIDC relying-party id_token validation.

Authlib's framework integrations compose ``AsyncOpenIDMixin`` with an OAuth2
client (the Starlette integration's ``StarletteOAuth2App`` mixes
``AsyncOpenIDMixin`` into its OAuth2 app and uses ``AsyncOAuth2Client`` as its
client class).  This module mirrors that composition for Bifrost's server-side
flows: the authorization-code request stays in :mod:`src.services.oauth_sso`,
while signature, JWKS, ``iss``, ``aud``, ``exp``/``iat``, ``nonce`` and ``azp``
validation are delegated to Authlib's ``parse_id_token``.

Authlib performs signature, JWKS, ``iss``, ``aud``, ``exp``/``iat``, ``nonce``
and ``azp`` validation. This module only supplies the discovery metadata and
the claims options that point those checks at Bifrost's configured issuer and
client ID (Authlib's ``parse_id_token`` does not check ``aud`` on its own).
There is no custom JWKS cache and no hand-written claim validation;
``jwks_provider`` exists only so tests can inject a fixed key set.
"""

from __future__ import annotations

import copy
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from authlib.integrations.base_client.async_openid import AsyncOpenIDMixin
from authlib.integrations.httpx_client import AsyncOAuth2Client

JwksProvider = Callable[[bool], Awaitable[Mapping[str, Any]]]

_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_MICROSOFT_ISSUER = re.compile(
    r"^https://(?:login\.microsoftonline\.com|sts\.windows\.net)/"
    r"(?P<tenant>[0-9a-fA-F-]{36})(?:/v2\.0)?/?$",
)


class IDTokenValidationError(ValueError):
    """Raised when a provider id_token cannot be verified."""


def microsoft_issuer_is_valid(claims: Mapping[str, Any], issuer: object) -> bool:
    """Accept only the issuer belonging to the token's own verified tenant.

    Entra ID issues per-tenant issuers (``https://login.microsoftonline.com/
    {tenant}/v2.0`` and ``https://sts.windows.net/{tenant}/``), including when
    the app is configured for ``common``, so an exact match against the
    discovery document's ``{tenantid}`` template would reject every login.
    The tenant is taken from the signed token's own ``tid`` claim.
    """

    tenant_id = claims.get("tid")
    if not isinstance(tenant_id, str) or not isinstance(issuer, str):
        return False
    match = _MICROSOFT_ISSUER.match(issuer)
    return bool(match and match.group("tenant").lower() == tenant_id.lower())


def google_server_metadata() -> dict[str, Any]:
    return {
        "issuer": "https://accounts.google.com",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def microsoft_server_metadata(tenant_id: str | None) -> dict[str, Any]:
    tenant = tenant_id or "common"
    return {
        "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "jwks_uri": f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        "id_token_signing_alg_values_supported": ["RS256"],
    }


GOOGLE_CLAIMS_OPTIONS: dict[str, Any] = {"iss": {"values": list(_GOOGLE_ISSUERS)}}
MICROSOFT_CLAIMS_OPTIONS: dict[str, Any] = {
    "iss": {"validate": microsoft_issuer_is_valid}
}


def _claims_options(
    client_id: str,
    server_metadata: Mapping[str, Any],
    claims_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Add the audience check and always supply an issuer option.

    Authlib's ``parse_id_token`` validates ``aud`` only through claims options;
    ``azp`` alone does not reject a token issued for another client. Supplying
    ``aud`` here keeps the check inside Authlib's ``JWTClaims.validate_aud``.
    Supplying ``iss`` means Authlib's metadata-derived default never applies.

    The options are deep-copied because Authlib's ``BaseClaims`` pops the
    ``validate`` hook out of each nested option dict; handing it a shared or
    module-level structure would disable the hook after the first parse.
    """

    options = copy.deepcopy(dict(claims_options or {}))
    options.setdefault("aud", {"values": [client_id]})
    if "iss" not in options and server_metadata.get("issuer"):
        options["iss"] = {"values": [server_metadata["issuer"]]}
    return options


class OIDCRelyingParty(AsyncOAuth2Client, AsyncOpenIDMixin):
    """Async OAuth2 client that can verify an OIDC ``id_token``.

    Only ``parse_id_token`` is used by Bifrost today; the token request remains
    in ``OAuthService`` so client-authentication methods stay a separate change.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None = None,
        server_metadata: Mapping[str, Any],
        claims_options: Mapping[str, Any] | None = None,
        jwks_provider: JwksProvider | None = None,
        **kwargs: Any,
    ) -> None:
        token_endpoint_auth_method = kwargs.pop("token_endpoint_auth_method", "none")
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=token_endpoint_auth_method,
            **kwargs,
        )
        self.server_metadata = dict(server_metadata)
        self.claims_options = _claims_options(
            client_id, self.server_metadata, claims_options
        )
        self.jwks_provider = jwks_provider

    async def load_server_metadata(self) -> dict[str, Any]:
        """Return the already-resolved metadata instead of re-fetching it."""
        return self.server_metadata

    async def fetch_jwk_set(self, force: bool = False) -> Mapping[str, Any]:
        if self.jwks_provider is not None:
            return await self.jwks_provider(force)
        # Authlib fetches ``jwks_uri`` once, caches it on ``server_metadata``
        # and forces one refresh when the token's ``kid`` is unknown.
        return await super().fetch_jwk_set(force=force)

    @asynccontextmanager
    async def _get_session(self) -> AsyncIterator["OIDCRelyingParty"]:
        # ``AsyncOAuth2Client`` is already an httpx client; Authlib's OpenID
        # mixin only needs an object exposing ``request()``.
        yield self

    async def parse_verified_id_token(
        self, token: Mapping[str, Any], nonce: str
    ) -> dict[str, Any]:
        """Verify ``token['id_token']`` and return its validated claims."""
        if not isinstance(nonce, str) or not nonce:
            # Authlib's validate_nonce only runs when params["nonce"] is truthy.
            raise IDTokenValidationError("Authorization request is missing a nonce")

        raw_id_token = token.get("id_token")
        if not isinstance(raw_id_token, str) or not raw_id_token:
            raise IDTokenValidationError("Authorization server did not return an id_token")

        try:
            user_info = await self.parse_id_token(
                dict(token),
                nonce=nonce,
                # Authlib pops the ``validate`` hooks out of the options it is
                # given, so hand it a fresh copy on every parse.
                claims_options=copy.deepcopy(self.claims_options),
            )
        except Exception as exc:  # Authlib/joserfc raise several exception types.
            raise IDTokenValidationError(str(exc)) from exc

        if user_info is None:
            raise IDTokenValidationError("Authorization server did not return an id_token")
        return dict(user_info)


OIDCClientFactory = Callable[..., "OIDCRelyingParty"]


def build_oidc_relying_party(
    *,
    client_id: str,
    client_secret: str | None = None,
    server_metadata: Mapping[str, Any],
    claims_options: Mapping[str, Any] | None = None,
    jwks_provider: JwksProvider | None = None,
) -> OIDCRelyingParty:
    """Factory used by ``OAuthService`` and overridable in tests."""

    return OIDCRelyingParty(
        client_id=client_id,
        client_secret=client_secret,
        server_metadata=server_metadata,
        claims_options=claims_options,
        jwks_provider=jwks_provider,
    )
