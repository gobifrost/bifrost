"""OAuth provider and token repositories.

Both extend ``OrgScopedRepository`` so cascade (org-specific → global) is
handled by the base class. No inline cascade primitives.

This is the canonical access path for OAuth provider/token reads from the
SDK execution surface. The pre-overhaul ``IntegrationsRepository.get_provider_org_token``
had no ``organization_id`` filter at all and could return any org's
``user_id=NULL`` token; it was the carrier of the cross-tenant token leak
fixed in the 2026-05 overhaul.

See ``api/src/repositories/README.md`` for the full pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.repositories.org_scoped import OrgScopedRepository


class OAuthProviderRepository(OrgScopedRepository[OAuthProvider]):
    """Cascade-scoped access to OAuth provider rows + OAuth-domain operations.

    Used for resolving a provider by ``provider_name`` (or
    ``integration_id``) from any caller that needs the canonical cascade
    (org-specific wins on name collision; falls back to global).

    Replaces the pre-overhaul ``OAuthConnectionRepository`` that lived in
    ``routers/oauth_connections.py``. The cascade reads come from the
    base class; OAuth-domain write/update methods are below.
    """

    model = OAuthProvider
    role_table = None  # OAuth providers have no role-based access

    # =========================================================================
    # OAuth-domain operations (absorbed from OAuthConnectionRepository)
    # =========================================================================

    async def get_by_connection_name(
        self, connection_name: str
    ) -> OAuthProvider | None:
        """Look up by either ``integration_id`` (UUID) or ``provider_name``.

        The ``connection_name`` parameter is overloaded for backwards
        compatibility — the REST router exposes the same string identifier
        whether the caller has the integration UUID or the provider name.

        Cascade applies via the base class for the ``provider_name`` path.
        ID lookups are globally unique; no cascade needed.
        """
        # Try integration_id (UUID) first.
        try:
            integration_id = UUID(connection_name)
            result = await self.session.execute(
                select(OAuthProvider).where(
                    OAuthProvider.integration_id == integration_id
                )
            )
            provider = result.scalar_one_or_none()
            if provider is not None:
                return provider
        except ValueError:
            # connection_name wasn't a UUID — expected; fall through to the
            # name-based cascade lookup below.
            pass

        # Fall back to provider_name with cascade.
        return await self.get(provider_name=connection_name)

    async def create_connection(
        self,
        connection_name: str,
        display_name: str,
        description: str | None,
        oauth_flow_type: str,
        client_id: str,
        client_secret: str | None,
        authorization_url: str | None,
        token_url: str | None,
        scopes_csv: str | None,
        created_by: str,
    ) -> OAuthProvider:
        """Create a new OAuth provider/connection in the current scope."""
        from src.core.security import encrypt_secret

        encrypted_secret = b""
        if client_secret:
            encrypted_secret = encrypt_secret(client_secret).encode()

        provider = OAuthProvider(
            organization_id=self.org_id,
            provider_name=connection_name,
            display_name=display_name,
            description=description,
            oauth_flow_type=oauth_flow_type,
            client_id=client_id,
            encrypted_client_secret=encrypted_secret,
            authorization_url=authorization_url,
            token_url=token_url,
            scopes=scopes_csv.split(",") if scopes_csv else [],
            status="not_connected",
            created_by=created_by,
        )
        self.session.add(provider)
        await self.session.flush()
        await self.session.refresh(provider)
        return provider

    async def update_connection(
        self,
        connection_name: str,
        *,
        name: str | None = None,
        oauth_flow_type: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        authorization_url: str | None = None,
        token_url: str | None = None,
        scopes: list[str] | None = None,
        audience: str | None = None,
    ) -> OAuthProvider | None:
        """Update an OAuth provider in-place."""
        from src.core.security import encrypt_secret

        provider = await self.get_by_connection_name(connection_name)
        if provider is None:
            return None

        if name is not None:
            provider.display_name = name
        if oauth_flow_type is not None:
            provider.oauth_flow_type = oauth_flow_type
        if client_id is not None:
            provider.client_id = client_id
        if client_secret is not None:
            provider.encrypted_client_secret = encrypt_secret(client_secret).encode()
        if authorization_url is not None:
            provider.authorization_url = authorization_url
        if token_url is not None:
            provider.token_url = token_url
        if scopes is not None:
            provider.scopes = scopes
        if audience is not None:
            provider.audience = audience

        provider.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(provider)
        return provider

    async def delete_connection(self, connection_name: str) -> bool:
        """Delete an OAuth provider and its tokens."""
        provider = await self.get_by_connection_name(connection_name)
        if provider is None:
            return False

        # Delete associated tokens first.
        token_result = await self.session.execute(
            select(OAuthToken).where(OAuthToken.provider_id == provider.id)
        )
        for token in token_result.scalars().all():
            await self.session.delete(token)

        await self.session.delete(provider)
        await self.session.flush()
        return True

    async def update_status(
        self,
        connection_name: str,
        status: str,
        status_message: str | None = None,
    ) -> bool:
        """Update provider status."""
        provider = await self.get_by_connection_name(connection_name)
        if provider is None:
            return False

        provider.status = status
        provider.status_message = status_message
        provider.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return True

    async def get_token(
        self, connection_name: str
    ) -> OAuthToken | None:
        """Get the token for a connection scoped to this repository's org_id.

        NOT cascaded — the org_id passed at construction time selects the
        token scope explicitly. Use ``OAuthTokenRepository.get_org_level_for_provider``
        for the cascade-with-fallback variant.
        """
        provider = await self.get_by_connection_name(connection_name)
        if provider is None:
            return None

        query = (
            select(OAuthToken)
            .where(OAuthToken.provider_id == provider.id)
        )
        if self.org_id is not None:
            query = query.where(OAuthToken.organization_id == self.org_id)
        else:
            query = query.where(OAuthToken.organization_id.is_(None))
        query = query.order_by(OAuthToken.created_at.desc())

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def store_token(
        self,
        connection_name: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime,
        scopes: list[str] | None = None,
    ) -> OAuthToken | None:
        """Persist a token for a connection. Updates existing or creates new."""
        from src.core.security import encrypt_secret

        provider = await self.get_by_connection_name(connection_name)
        if provider is None:
            return None

        encrypted_access = encrypt_secret(access_token).encode()
        encrypted_refresh = (
            encrypt_secret(refresh_token).encode() if refresh_token else None
        )
        existing_token = await self.get_token(connection_name)

        now = datetime.now(timezone.utc)
        if existing_token:
            existing_token.encrypted_access_token = encrypted_access
            existing_token.encrypted_refresh_token = encrypted_refresh
            existing_token.expires_at = expires_at
            existing_token.scopes = scopes or []
            existing_token.status = "completed"
            existing_token.status_message = None
            existing_token.last_refresh_at = now
            token = existing_token
        else:
            token = OAuthToken(
                organization_id=self.org_id,
                provider_id=provider.id,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=expires_at,
                scopes=scopes or [],
                status="completed",
                status_message=None,
                last_refresh_at=now,
            )
            self.session.add(token)

        provider.status = "completed"
        provider.status_message = "Token acquired successfully"
        provider.last_token_refresh = now
        provider.updated_at = now

        await self.session.flush()
        await self.session.refresh(token)
        return token

    async def to_detail(self, provider: OAuthProvider) -> Any:
        """Convert a provider row into the ``OAuthConnectionDetail`` model.

        Local import keeps the repository layer from depending on the
        contracts package (which depends on Pydantic and pulls in a wide
        transitive surface).
        """
        from src.models.contracts.oauth import (
            OAuthConnectionDetail,
            OAuthFlowType,
            OAuthStatus,
        )

        token = await self.get_token(provider.provider_name)
        expires_at = token.expires_at if token else None

        oauth_flow_type: OAuthFlowType = provider.oauth_flow_type  # type: ignore[assignment]
        status: OAuthStatus = provider.status or "not_connected"  # type: ignore[assignment]

        scopes_str = " ".join(provider.scopes) if provider.scopes else ""

        return OAuthConnectionDetail(
            connection_name=provider.provider_name,
            name=provider.display_name,
            provider=provider.provider_name,
            description=provider.description,
            oauth_flow_type=oauth_flow_type,
            client_id=provider.client_id,
            authorization_url=provider.authorization_url,
            token_url=provider.token_url or "",
            scopes=scopes_str,
            audience=provider.audience,
            status=status,
            status_message=provider.status_message,
            integration_id=(
                str(provider.integration_id) if provider.integration_id else None
            ),
            expires_at=expires_at,
            last_refresh_at=provider.last_token_refresh,
            last_test_at=None,
            created_at=provider.created_at,
            created_by=provider.created_by or "",
            updated_at=provider.updated_at,
        )


class OAuthTokenRepository(OrgScopedRepository[OAuthToken]):
    """Cascade-scoped access to OAuth token rows (org-level, user_id=NULL).

    Used for resolving the org-level OAuth token for a given provider
    from the SDK execution path. Org-specific tokens win; falls back to
    global (``organization_id IS NULL``).

    The base class ``get(**filters)`` enforces the cascade. This subclass
    adds one helper for the common "give me the org-level token for this
    provider" lookup, which combines the provider_id filter with the
    ``user_id IS NULL`` constraint that distinguishes org-level tokens
    from per-user tokens.
    """

    model = OAuthToken
    role_table = None  # OAuth tokens have no role-based access

    def __init__(
        self,
        session: AsyncSession,
        org_id: UUID | str | None,
        user_id: UUID | str | None = None,
        bypass_resource_admission: bool = False,
        is_external: bool = False,
    ):
        super().__init__(
            session,
            org_id,
            user_id,
            bypass_resource_admission=bypass_resource_admission,
            is_external=is_external,
        )

    async def get_org_level_for_provider(
        self, provider_id: UUID
    ) -> Any:
        """Get the org-level OAuth token for a provider (``user_id IS NULL``).

        Applies the standard cascade: prefer the token bound to this
        repository's ``org_id``; fall back to the global token if no
        org-specific row exists. NEVER returns another org's token —
        the cascade explicitly filters by either this org or NULL.

        EXTERNAL principals (EXT-1 OPEN-E) get the org tier ONLY: the global
        (``organization_id IS NULL``) token is a decrypted third-party
        credential and must never fall through to a portal user. An external
        repo with no org returns ``None``. The engine-sentinel / normal-user
        path is unchanged — it still cascades to global, because a workflow
        legitimately uses its org's integrations including global defaults.

        Args:
            provider_id: ``OAuthProvider`` UUID.

        Returns:
            ``OAuthToken`` or ``None`` if not found in either scope.
        """
        # Try org-specific first (if we have an org).
        if self.org_id is not None:
            result = await self.session.execute(
                select(OAuthToken).where(
                    OAuthToken.provider_id == provider_id,
                    OAuthToken.organization_id == self.org_id,
                    OAuthToken.user_id.is_(None),
                )
            )
            token = result.scalars().first()
            if token is not None:
                return token

        # External principals have no global tier (EXT-1 OPEN-E): never fall
        # back to the global OAuth token (decrypted third-party credential).
        if self.external_restricted:
            return None

        # Fall back to global.
        result = await self.session.execute(
            select(OAuthToken).where(
                OAuthToken.provider_id == provider_id,
                OAuthToken.organization_id.is_(None),
                OAuthToken.user_id.is_(None),
            )
        )
        return result.scalars().first()
