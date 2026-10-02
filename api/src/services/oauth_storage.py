"""
OAuth Storage Service
Handles CRUD operations for OAuth connections using PostgreSQL with encrypted fields.
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet

from src.models import CreateOAuthConnectionRequest, OAuthConnection, UpdateOAuthConnectionRequest

logger = logging.getLogger(__name__)


def _get_encryption_key() -> bytes:
    """Get encryption key from settings."""
    from src.config import get_settings
    settings = get_settings()
    # Use first 32 bytes of secret key as Fernet key (base64 encoded)
    import base64
    key_bytes = settings.secret_key.encode()[:32].ljust(32, b'0')
    return base64.urlsafe_b64encode(key_bytes)


def _encrypt(value: str) -> bytes:
    """Encrypt a string value."""
    f = Fernet(_get_encryption_key())
    return f.encrypt(value.encode())


class OAuthStorageService:
    """
    Service for managing OAuth connections in PostgreSQL.

    Responsibilities:
    - CRUD operations for OAuthProvider table
    - Store client_secret encrypted in database
    - Store OAuth tokens encrypted in OAuthToken table
    - Implement org→GLOBAL fallback pattern
    """

    def __init__(self, session: Any = None):
        """Initialize OAuth storage service.

        Args:
            session: Optional async database session. If provided, this session
                     will be used for all operations. If not provided, creates
                     new sessions internally using get_session_factory().
        """
        self._session = session
        logger.info("OAuthStorageService initialized with PostgreSQL")

    def _get_session_context(self) -> Any:
        """Get session context manager.

        Returns the injected session if available, otherwise creates a new one.
        """
        from contextlib import asynccontextmanager

        if self._session is not None:
            @asynccontextmanager
            async def injected_session():
                yield self._session
            return injected_session()

        from src.core.database import get_session_factory
        return get_session_factory()()

    async def create_connection(
        self,
        org_id: str | None,
        request: CreateOAuthConnectionRequest,
        created_by: str
    ) -> OAuthConnection:
        """
        Create a new OAuth connection/provider.

        Args:
            org_id: Organization ID or None for GLOBAL
            request: Connection creation request
            created_by: Email of user creating the connection

        Returns:
            Created OAuthConnection
        """
        from src.models import OAuthProvider

        async with self._get_session_context() as db:
            org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None

            # Encrypt client secret (required for most flows, validated by model)
            encrypted_secret = _encrypt(request.client_secret) if request.client_secret else None

            provider = OAuthProvider(
                organization_id=org_uuid,
                provider_name=request.connection_name,
                client_id=request.client_id,
                encrypted_client_secret=encrypted_secret,
                scopes=request.scopes.split(",") if request.scopes else [],
                token_url_defaults=request.token_url_defaults or {},
                provider_metadata={
                    "authorization_url": request.authorization_url,
                    "token_url": request.token_url,
                    "redirect_uri": request.redirect_uri or f"/oauth/callback/{request.connection_name}",
                    "oauth_flow_type": request.oauth_flow_type,
                    "description": request.description,
                    "created_by": created_by,
                    "status": "not_connected"
                }
            )
            db.add(provider)
            await db.commit()
            await db.refresh(provider)

            return self._to_connection_model(provider)

    async def get_connection(
        self,
        org_id: str | None,
        connection_name: str
    ) -> OAuthConnection | None:
        """
        Get OAuth connection by name with org→GLOBAL fallback.

        Args:
            org_id: Organization ID or None for GLOBAL
            connection_name: Name of the connection

        Returns:
            OAuthConnection or None if not found
        """
        from sqlalchemy import select, or_
        from src.models import OAuthProvider

        async with self._get_session_context() as db:
            org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None

            # Try org-specific first, then GLOBAL fallback
            if org_uuid:
                query = select(OAuthProvider).where(
                    OAuthProvider.provider_name == connection_name,
                    or_(
                        OAuthProvider.organization_id == org_uuid,
                        OAuthProvider.organization_id.is_(None)
                    )
                ).order_by(OAuthProvider.organization_id.desc().nulls_last())
            else:
                query = select(OAuthProvider).where(
                    OAuthProvider.provider_name == connection_name,
                    OAuthProvider.organization_id.is_(None)
                )

            result = await db.execute(query)
            provider = result.scalars().first()

            if provider:
                return self._to_connection_model(provider)
            return None

    async def get_by_integration_id(
        self,
        integration_id: str | UUID
    ) -> OAuthConnection | None:
        """
        Get OAuth connection by integration ID.

        Args:
            integration_id: UUID of the integration

        Returns:
            OAuthConnection or None if not found
        """
        from sqlalchemy import select
        from src.models import OAuthProvider

        async with self._get_session_context() as db:
            integration_uuid = UUID(integration_id) if isinstance(integration_id, str) else integration_id

            query = select(OAuthProvider).where(
                OAuthProvider.integration_id == integration_uuid
            )

            result = await db.execute(query)
            provider = result.scalars().first()

            if provider:
                return self._to_connection_model(provider)
            return None

    async def update_connection(
        self,
        org_id: str | None,
        connection_name: str,
        request: UpdateOAuthConnectionRequest,
        updated_by: str
    ) -> OAuthConnection | None:
        """
        Update an OAuth connection.

        Args:
            org_id: Organization ID or None for GLOBAL
            connection_name: Name of the connection to update
            request: Update request
            updated_by: Email of user updating

        Returns:
            Updated OAuthConnection or None if not found
        """
        from sqlalchemy import select
        from src.models import OAuthProvider

        async with self._get_session_context() as db:
            org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None

            query = select(OAuthProvider).where(
                OAuthProvider.provider_name == connection_name,
                OAuthProvider.organization_id == org_uuid
            )
            result = await db.execute(query)
            provider = result.scalars().first()

            if not provider:
                return None

            # Update fields
            if request.client_id:
                provider.client_id = request.client_id
            if request.client_secret:
                provider.encrypted_client_secret = _encrypt(request.client_secret)
            if request.scopes:
                provider.scopes = request.scopes  # Already a list from validator

            # Update metadata
            metadata = dict(provider.provider_metadata)
            if request.authorization_url:
                metadata["authorization_url"] = request.authorization_url
            if request.token_url:
                metadata["token_url"] = request.token_url
            metadata["updated_by"] = updated_by
            provider.provider_metadata = metadata

            # Update token_url_defaults if provided
            if request.token_url_defaults is not None:
                provider.token_url_defaults = request.token_url_defaults

            await db.commit()
            await db.refresh(provider)

            return self._to_connection_model(provider)

    async def delete_connection(
        self,
        org_id: str | None,
        connection_name: str
    ) -> bool:
        """
        Delete an OAuth connection and its tokens.

        Args:
            org_id: Organization ID or None for GLOBAL
            connection_name: Name of the connection to delete

        Returns:
            True if deleted, False if not found
        """
        from sqlalchemy import select, delete
        from src.models import OAuthProvider, OAuthToken

        async with self._get_session_context() as db:
            org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None

            query = select(OAuthProvider).where(
                OAuthProvider.provider_name == connection_name,
                OAuthProvider.organization_id == org_uuid
            )
            result = await db.execute(query)
            provider = result.scalars().first()

            if not provider:
                return False

            # Delete associated tokens first
            await db.execute(
                delete(OAuthToken).where(OAuthToken.provider_id == provider.id)
            )

            # Delete provider (session.delete() is NOT async)
            db.delete(provider)
            await db.commit()

            return True

    async def store_tokens(
        self,
        org_id: str | None,
        connection_name: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        user_id: str | None = None
    ) -> bool:
        """
        Store OAuth tokens for a connection.

        Args:
            org_id: Organization ID or None for GLOBAL
            connection_name: Name of the connection
            access_token: Access token to store
            refresh_token: Optional refresh token
            expires_at: Token expiration time
            scopes: Token scopes
            user_id: Optional user ID if user-specific token

        Returns:
            True if stored successfully
        """
        from sqlalchemy import select
        from src.models import OAuthProvider, OAuthToken

        async with self._get_session_context() as db:
            org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None
            user_uuid = UUID(user_id) if user_id else None

            # Find provider
            query = select(OAuthProvider).where(
                OAuthProvider.provider_name == connection_name,
                OAuthProvider.organization_id == org_uuid
            )
            result = await db.execute(query)
            provider = result.scalars().first()

            if not provider:
                return False

            # Create or update token
            token_query = select(OAuthToken).where(
                OAuthToken.provider_id == provider.id,
                OAuthToken.user_id == user_uuid
            )
            result = await db.execute(token_query)
            token = result.scalars().first()

            if token:
                # Update existing token
                token.encrypted_access_token = _encrypt(access_token)
                if refresh_token:
                    token.encrypted_refresh_token = _encrypt(refresh_token)
                if expires_at:
                    token.expires_at = expires_at
                if scopes:
                    token.scopes = scopes
            else:
                # Create new token
                token = OAuthToken(
                    organization_id=org_uuid,
                    provider_id=provider.id,
                    user_id=user_uuid,
                    encrypted_access_token=_encrypt(access_token),
                    encrypted_refresh_token=_encrypt(refresh_token) if refresh_token else None,
                    expires_at=expires_at,
                    scopes=scopes or []
                )
                db.add(token)

            # Update provider status
            metadata = dict(provider.provider_metadata)
            metadata["status"] = "connected"
            provider.provider_metadata = metadata

            await db.commit()
            return True

    def _to_connection_model(self, provider) -> OAuthConnection:
        """Convert OAuthProvider to OAuthConnection model."""
        metadata = provider.provider_metadata or {}
        org_id = str(provider.organization_id) if provider.organization_id else "GLOBAL"
        return OAuthConnection(
            org_id=org_id,
            connection_name=provider.provider_name,
            description=metadata.get("description"),
            oauth_flow_type=metadata.get("oauth_flow_type", "authorization_code"),
            client_id=provider.client_id,
            client_secret_config_key=f"oauth_{provider.provider_name}_client_secret",
            oauth_response_config_key=f"oauth_{provider.provider_name}_oauth_response",
            authorization_url=metadata.get("authorization_url"),
            token_url=metadata.get("token_url", "https://oauth.example.com/token"),
            token_url_defaults=provider.token_url_defaults or {},
            scopes=",".join(provider.scopes) if provider.scopes else "",
            redirect_uri=metadata.get("redirect_uri", f"/oauth/callback/{provider.provider_name}"),
            expires_at=None,
            status=metadata.get("status", "not_connected"),
            created_by=metadata.get("created_by", "system"),
            created_at=provider.created_at,
            updated_at=provider.updated_at
        )
