"""Administration and assertion matching for identity federation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.identities import (
    FederatedCredentialCreate,
    FederatedCredentialPublic,
    FederatedCredentialUpdate,
)
from src.models.orm.federated_credentials import FederatedCredential
from src.models.orm.users import User
from src.services.audit import emit_audit
from src.services.authorization.enforce import Caller
from src.services.identities import (
    LIST_OPERATION,
    UPDATE_OPERATION,
    _managed_identity,
    require_delegation,
)


class FederatedCredentialError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _public(row: FederatedCredential) -> FederatedCredentialPublic:
    return FederatedCredentialPublic(
        id=row.id,
        identity_id=row.identity_id,
        name=row.name,
        issuer=row.issuer,
        discovery_url=row.discovery_url,
        audience=row.audience,
        subject=row.subject,
        claims=dict(row.claims),
        subject_token_type=row.subject_token_type,
        token_ttl_seconds=row.token_ttl_seconds,
        enabled=row.enabled,
    )


async def list_federated_credentials(
    db: AsyncSession, caller: Caller, identity_id: UUID
) -> list[FederatedCredentialPublic]:
    await _managed_identity(db, caller, identity_id, LIST_OPERATION)
    rows = (
        await db.scalars(
            select(FederatedCredential)
            .where(FederatedCredential.identity_id == identity_id)
            .order_by(FederatedCredential.name, FederatedCredential.id)
        )
    ).all()
    return [_public(row) for row in rows]


async def create_federated_credential(
    db: AsyncSession, caller: Caller, identity_id: UUID, body: FederatedCredentialCreate
) -> FederatedCredentialPublic:
    await _managed_identity(db, caller, identity_id, UPDATE_OPERATION)
    await require_delegation(db, caller, identity_id)
    row = FederatedCredential(identity_id=identity_id, **body.model_dump())
    db.add(row)
    await db.flush()
    await emit_audit(
        db,
        "identity.federated_credential.create",
        resource_type="federated_credential",
        resource_id=row.id,
        details={"identity_id": str(identity_id), "issuer": row.issuer, "subject_token_type": row.subject_token_type},
    )
    return _public(row)


async def _managed_credential(
    db: AsyncSession, caller: Caller, identity_id: UUID, credential_id: UUID
) -> FederatedCredential:
    await _managed_identity(db, caller, identity_id, UPDATE_OPERATION)
    await require_delegation(db, caller, identity_id)
    row = await db.scalar(
        select(FederatedCredential)
        .where(FederatedCredential.id == credential_id)
        .with_for_update()
    )
    if row is None or row.identity_id != identity_id:
        raise FederatedCredentialError(404, "Federated credential not found")
    return row


async def update_federated_credential(
    db: AsyncSession,
    caller: Caller,
    identity_id: UUID,
    credential_id: UUID,
    body: FederatedCredentialUpdate,
) -> FederatedCredentialPublic:
    row = await _managed_credential(db, caller, identity_id, credential_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise FederatedCredentialError(422, "No credential fields were supplied")
    before = {key: getattr(row, key) for key in changes}
    for key, value in changes.items():
        setattr(row, key, value)
    row.revision += 1
    await db.flush()
    await emit_audit(
        db,
        "identity.federated_credential.update",
        resource_type="federated_credential",
        resource_id=row.id,
        details={"identity_id": str(identity_id), "changed": sorted(changes), "before_enabled": before.get("enabled")},
    )
    return _public(row)


async def delete_federated_credential(
    db: AsyncSession, caller: Caller, identity_id: UUID, credential_id: UUID
) -> None:
    row = await _managed_credential(db, caller, identity_id, credential_id)
    await db.delete(row)
    await db.flush()
    await emit_audit(
        db,
        "identity.federated_credential.delete",
        resource_type="federated_credential",
        resource_id=credential_id,
        details={"identity_id": str(identity_id)},
    )


async def enabled_credentials_for_exchange(
    db: AsyncSession, subject_token_type: str
) -> list[FederatedCredential]:
    """Load only configured enabled mappings; never discover an assertion issuer."""
    rows = await db.scalars(
        select(FederatedCredential)
        .join(User, User.id == FederatedCredential.identity_id)
        .where(
            FederatedCredential.enabled.is_(True),
            FederatedCredential.subject_token_type == subject_token_type,
            User.is_active.is_(True),
            User.is_system.is_(False),
            User.identity_kind.is_not(None),
        )
    )
    return list(rows.all())


def exact_claims_match(row: FederatedCredential, claims: dict[str, Any]) -> bool:
    """Check every configured assertion field as an exact scalar string."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or subject != row.subject:
        return False
    for name, required in row.claims.items():
        value = claims.get(name)
        if not isinstance(value, str) or value != required:
            return False
    return True
