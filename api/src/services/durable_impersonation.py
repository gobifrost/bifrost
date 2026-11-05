"""Server-trusted browser-impersonation attribution for durable work."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.core.principal import UserPrincipal
from src.services.audit_context import current_actor


@dataclass(frozen=True)
class DurableImpersonationProvenance:
    """Attribution copied from a verified request into durable records only."""

    impersonated_by_user_id: UUID | None = None
    impersonation_session_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.impersonated_by_user_id is None) != (self.impersonation_session_id is None):
            raise ValueError("Durable impersonation provenance requires both operator and session")

    def pending_value(self) -> dict[str, str] | None:
        if self.impersonated_by_user_id is None and self.impersonation_session_id is None:
            return None
        return {
            "impersonated_by_user_id": str(self.impersonated_by_user_id),
            "impersonation_session_id": str(self.impersonation_session_id),
        }

    def columns(self) -> dict[str, UUID | None]:
        return {
            "impersonated_by_user_id": self.impersonated_by_user_id,
            "impersonation_session_id": self.impersonation_session_id,
        }


def current_durable_impersonation() -> DurableImpersonationProvenance:
    """Read provenance from the server-set audit actor, never request input."""
    actor = current_actor()
    if actor is None:
        return DurableImpersonationProvenance()
    return DurableImpersonationProvenance(
        impersonated_by_user_id=actor.impersonated_by_user_id,
        impersonation_session_id=actor.impersonation_session_id,
    )


def principal_durable_impersonation(principal: UserPrincipal) -> DurableImpersonationProvenance:
    """Read provenance from a server-verified runtime principal."""
    return DurableImpersonationProvenance(
        impersonated_by_user_id=principal.impersonated_by_user_id,
        impersonation_session_id=principal.impersonation_session_id,
    )


def pending_durable_impersonation(
    value: dict[str, str] | None,
) -> DurableImpersonationProvenance:
    """Decode the Redis transit value written by :meth:`pending_value`."""
    if value is None:
        return DurableImpersonationProvenance()
    return DurableImpersonationProvenance(
        impersonated_by_user_id=_uuid(value.get("impersonated_by_user_id")),
        impersonation_session_id=_uuid(value.get("impersonation_session_id")),
    )


def _uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    return UUID(value)
