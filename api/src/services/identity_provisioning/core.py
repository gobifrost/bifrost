"""Protocol-independent provisioning commands for one identity provider
connection.

Every command runs inside the caller's transaction. The rules it enforces:

- A connection only creates or changes users whose email domain is a
  verified claim of its organization, and never touches a user whose home is
  another organization.
- An existing user in the same organization is adopted by email; one in
  another organization is a conflict.
- The identity provider owns name, email and active for linked users.
- Roles come from group mappings. Provisioning only removes the role
  assignments it created (``UserRole.identity_provider_id``), so a manual
  grant always survives.
- Once a connection has at least one group mapping, a user needs at least one
  mapped group to stay active.
- Deleting a user disables and unlinks them, except a user this connection
  created who has never signed in: that account has no history to keep and
  is removed permanently.

Audit rows are written in the transaction; platform events are collected in
``pending_events`` and sent with :meth:`Provisioner.emit_pending_events`
after the caller commits.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import (
    BASE_ROLE_IDS,
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
)
from src.core.log_safety import log_safe
from src.models.orm.identity_providers import (
    IdentityProvider,
    IdentityProviderGroup,
    IdentityProviderGroupMapping,
    IdentityProviderGroupMember,
    IdentityProviderGroupRole,
    IdentityProviderUser,
)
from src.models.orm.users import User, UserRole
from src.services.audit_context import ActorContext
from src.services.identity_provisioning.domains import email_domain, verified_domains

logger = logging.getLogger(__name__)

# Roles no group mapping may ever grant: base roles are held through
# ``User.base_role_id``, and the platform-wide builtin roles are assigned by a
# person, never by a directory.
UNMAPPABLE_ROLE_IDS = frozenset(
    {*BASE_ROLE_IDS, PLATFORM_ADMIN_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID}
)


class ProvisioningErrorKind(enum.Enum):
    CONFLICT = "conflict"
    INVALID = "invalid"
    DOMAIN_NOT_CLAIMED = "domain_not_claimed"
    NOT_FOUND = "not_found"


class ProvisioningError(Exception):
    def __init__(self, kind: ProvisioningErrorKind, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class UserRecord:
    """A directory user, independent of the protocol that delivered it."""

    user_name: str
    email: str
    external_id: str | None
    display_name: str | None
    active: bool
    attributes: dict[str, Any]


@dataclass(frozen=True)
class GroupRecord:
    display_name: str
    external_id: str | None
    attributes: dict[str, Any]


@dataclass
class _AccessChange:
    activated: bool = False
    deactivated: bool = False
    roles_added: set[UUID] = field(default_factory=set)
    roles_removed: set[UUID] = field(default_factory=set)


def normalize_email(value: str) -> str:
    return value.strip().lower()


class Provisioner:
    def __init__(self, session: AsyncSession, connection: IdentityProvider):
        self.session = session
        self.connection = connection
        self.pending_events: list[tuple[str, dict[str, Any]]] = []
        self._actor = ActorContext(
            user_id=None,
            organization_id=connection.organization_id,
            email=None,
            name=f"Identity provider: {connection.name}",
            source="identity_provider",
            surface="service",
        )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def get_user_link(self, link_id: UUID) -> IdentityProviderUser | None:
        return await self.session.scalar(
            select(IdentityProviderUser).where(
                IdentityProviderUser.id == link_id,
                IdentityProviderUser.identity_provider_id == self.connection.id,
            )
        )

    async def get_group(self, group_id: UUID) -> IdentityProviderGroup | None:
        return await self.session.scalar(
            select(IdentityProviderGroup).where(
                IdentityProviderGroup.id == group_id,
                IdentityProviderGroup.identity_provider_id == self.connection.id,
            )
        )

    async def group_member_ids(self, group_id: UUID) -> set[UUID]:
        rows = await self.session.scalars(
            select(IdentityProviderGroupMember.identity_provider_user_id).where(
                IdentityProviderGroupMember.group_id == group_id
            )
        )
        return set(rows)

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def create_user(self, record: UserRecord) -> IdentityProviderUser:
        await self._refuse_duplicate_link(record, exclude_link_id=None)
        email = normalize_email(record.email)
        await self._require_claimed_domain(email)

        user = await self.session.scalar(select(User).where(User.email == email))
        created_user = user is None
        if user is None:
            user = await self._new_user(email, record)
        else:
            await self._require_adoptable(user)
            user.name = record.display_name or user.name

        link = IdentityProviderUser(
            identity_provider_id=self.connection.id,
            user_id=user.id,
            external_id=record.external_id,
            user_name=record.user_name,
            active=record.active,
            attributes=record.attributes,
            created_user=created_user,
        )
        self.session.add(link)
        await self.session.flush()
        await self._reconcile_access([link.id])
        await self._audit(
            "identity_provider.user_provisioned",
            user,
            {"email": email, "adopted": not created_user},
        )
        self._queue_user_event("user.provisioned", user, link, adopted=not created_user)
        return link

    async def replace_user(
        self, link: IdentityProviderUser, record: UserRecord
    ) -> IdentityProviderUser:
        await self._refuse_duplicate_link(record, exclude_link_id=link.id)
        user = await self.session.get(User, link.user_id, with_for_update=True)
        assert user is not None
        email = normalize_email(record.email)
        if email != user.email:
            await self._require_claimed_domain(email)
            taken = await self.session.scalar(
                select(User.id).where(User.email == email, User.id != user.id)
            )
            if taken is not None:
                raise ProvisioningError(
                    ProvisioningErrorKind.CONFLICT, "Another user already has this email"
                )
            user.email = email
        user.name = record.display_name or user.name
        link.external_id = record.external_id
        link.user_name = record.user_name
        link.active = record.active
        link.attributes = record.attributes
        await self.session.flush()
        await self._reconcile_access([link.id])
        await self._audit("identity_provider.user_updated", user, {"email": user.email})
        self._queue_user_event("user.updated", user, link)
        return link

    async def delete_user(self, link: IdentityProviderUser) -> None:
        user = await self.session.get(User, link.user_id, with_for_update=True)
        assert user is not None
        if link.created_user and user.last_login is None:
            from shared.run_cancellation import cancel_unstarted_runs_for_deleted_user

            body = self._user_event_body(user, link)
            await cancel_unstarted_runs_for_deleted_user(self.session, user.id)
            await self.session.delete(user)
            await self.session.flush()
            await self._audit(
                "identity_provider.user_deleted", None, {"email": body["user"]["email"]},
                resource_id=body["user"]["id"],
            )
            self.pending_events.append(
                ("user.deprovisioned", {**body, "deprovision": {"reason": "deleted", "account_removed": True}})
            )
            return

        await self._set_active(user, False)
        removed = await self._remove_granted_roles(user.id, None)
        await self.session.delete(link)
        await self.session.flush()
        for role_id in removed:
            await self._invalidate_role(role_id)
        await self._audit("identity_provider.user_unlinked", user, {"email": user.email})
        self.pending_events.append(
            (
                "user.deprovisioned",
                {
                    **self._user_event_body(user, None),
                    "deprovision": {"reason": "deleted", "account_removed": False},
                },
            )
        )

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def create_group(
        self, record: GroupRecord, member_link_ids: Iterable[UUID]
    ) -> IdentityProviderGroup:
        await self._refuse_duplicate_group(record, exclude_group_id=None)
        group = IdentityProviderGroup(
            identity_provider_id=self.connection.id,
            external_id=record.external_id,
            display_name=record.display_name,
            attributes=record.attributes,
        )
        self.session.add(group)
        await self.session.flush()
        await self.set_group_members(group, member_link_ids)
        return group

    async def replace_group(
        self, group: IdentityProviderGroup, record: GroupRecord
    ) -> IdentityProviderGroup:
        await self._refuse_duplicate_group(record, exclude_group_id=group.id)
        group.display_name = record.display_name
        group.external_id = record.external_id
        group.attributes = record.attributes
        await self.session.flush()
        return group

    async def set_group_members(
        self, group: IdentityProviderGroup, member_link_ids: Iterable[UUID]
    ) -> None:
        wanted = await self._known_links(set(member_link_ids))
        current = await self.group_member_ids(group.id)
        added, removed = wanted - current, current - wanted
        if removed:
            await self.session.execute(
                delete(IdentityProviderGroupMember).where(
                    IdentityProviderGroupMember.group_id == group.id,
                    IdentityProviderGroupMember.identity_provider_user_id.in_(removed),
                )
            )
        for link_id in added:
            self.session.add(
                IdentityProviderGroupMember(group_id=group.id, identity_provider_user_id=link_id)
            )
        await self.session.flush()
        if added or removed:
            await self._reconcile_access(added | removed, group_changes={
                link_id: ("added" if link_id in added else "removed") for link_id in added | removed
            }, group=group)

    async def delete_group(self, group: IdentityProviderGroup) -> None:
        members = await self.group_member_ids(group.id)
        await self.session.delete(group)
        await self.session.flush()
        if members:
            await self._reconcile_access(members)

    # ------------------------------------------------------------------
    # Access reconciliation
    # ------------------------------------------------------------------

    async def reconcile_all(self) -> None:
        """Recompute access for every linked user (after a mapping change)."""
        link_ids = await self.session.scalars(
            select(IdentityProviderUser.id).where(
                IdentityProviderUser.identity_provider_id == self.connection.id
            )
        )
        await self._reconcile_access(set(link_ids))

    async def _reconcile_access(
        self,
        link_ids: Iterable[UUID],
        *,
        group_changes: dict[UUID, str] | None = None,
        group: IdentityProviderGroup | None = None,
    ) -> None:
        link_ids = set(link_ids)
        if not link_ids:
            return
        mapped_roles = await self._mapped_group_roles()
        has_mappings = bool(mapped_roles)
        memberships: dict[UUID, set[UUID]] = {}
        for link_id, group_id in (
            await self.session.execute(
                select(
                    IdentityProviderGroupMember.identity_provider_user_id,
                    IdentityProviderGroupMember.group_id,
                ).where(IdentityProviderGroupMember.identity_provider_user_id.in_(link_ids))
            )
        ).all():
            memberships.setdefault(link_id, set()).add(group_id)

        links = (
            await self.session.scalars(
                select(IdentityProviderUser).where(IdentityProviderUser.id.in_(link_ids))
            )
        ).all()
        for link in links:
            user = await self.session.get(User, link.user_id, with_for_update=True)
            assert user is not None
            mapped = memberships.get(link.id, set()) & mapped_roles.keys()
            desired_roles: set[UUID] = set()
            for group_id in mapped:
                desired_roles |= mapped_roles[group_id]
            desired_active = link.active and (not has_mappings or bool(mapped))
            change = _AccessChange()
            if user.is_active != desired_active:
                await self._set_active(user, desired_active)
                change.activated = desired_active
                change.deactivated = not desired_active
            change.roles_removed = await self._remove_granted_roles(user.id, desired_roles)
            change.roles_added = await self._grant_roles(user, desired_roles)
            for role_id in change.roles_added | change.roles_removed:
                await self._invalidate_role(role_id)
            if change.roles_added or change.roles_removed:
                from shared.role_cache import invalidate_user

                await invalidate_user(user.id)
            if group is not None and group_changes and link.id in group_changes:
                self.pending_events.append(
                    (
                        "user.groups_changed",
                        {
                            **self._user_event_body(user, link),
                            "group": {
                                "id": str(group.id),
                                "display_name": group.display_name,
                                "change": group_changes[link.id],
                            },
                            "roles_added": sorted(str(r) for r in change.roles_added),
                            "roles_removed": sorted(str(r) for r in change.roles_removed),
                        },
                    )
                )
            if change.deactivated:
                await self._audit(
                    "identity_provider.user_deactivated", user, {"email": user.email}
                )
                reason = "inactive" if not link.active else "no_mapped_group"
                self.pending_events.append(
                    (
                        "user.deprovisioned",
                        {
                            **self._user_event_body(user, link),
                            "deprovision": {"reason": reason, "account_removed": False},
                        },
                    )
                )
            elif change.activated:
                await self._audit(
                    "identity_provider.user_activated", user, {"email": user.email}
                )

    async def _mapped_group_roles(self) -> dict[UUID, set[UUID]]:
        rows = (
            await self.session.execute(
                select(IdentityProviderGroupMapping.group_id, IdentityProviderGroupRole.role_id)
                .join(
                    IdentityProviderGroup,
                    IdentityProviderGroup.id == IdentityProviderGroupMapping.group_id,
                )
                .outerjoin(
                    IdentityProviderGroupRole,
                    IdentityProviderGroupRole.group_id == IdentityProviderGroupMapping.group_id,
                )
                .where(IdentityProviderGroup.identity_provider_id == self.connection.id)
            )
        ).all()
        mapped: dict[UUID, set[UUID]] = {}
        for group_id, role_id in rows:
            roles = mapped.setdefault(group_id, set())
            if role_id is not None and role_id not in UNMAPPABLE_ROLE_IDS:
                roles.add(role_id)
        return mapped

    async def _grant_roles(self, user: User, desired: set[UUID]) -> set[UUID]:
        from shared.account_guards import AccountGuardError
        from src.services.user_role_assignments import (
            RoleAssignmentError,
            default_boundaries,
            insert_assignment,
        )

        held = set(
            await self.session.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id))
        )
        added: set[UUID] = set()
        for role_id in sorted(desired - held - {user.base_role_id}):
            try:
                await insert_assignment(
                    self.session,
                    user_id=user.id,
                    role_id=role_id,
                    boundaries=default_boundaries(user.organization_id),
                    assigned_by=f"identity-provider:{self.connection.name}"[:255],
                    identity_provider_id=self.connection.id,
                )
            except (RoleAssignmentError, AccountGuardError) as exc:
                # e.g. a privileged role mapped for an External user: skip the
                # grant, keep provisioning the rest.
                logger.warning(
                    "Identity provider %s skipped role %s for user %s: %s",
                    self.connection.id,
                    role_id,
                    user.id,
                    log_safe(str(exc)),
                )
                continue
            added.add(role_id)
        return added

    async def _remove_granted_roles(self, user_id: UUID, desired: set[UUID] | None) -> set[UUID]:
        """Delete assignments this connection made that are no longer wanted
        (all of them when ``desired`` is None)."""
        stmt = select(UserRole.role_id).where(
            UserRole.user_id == user_id,
            UserRole.identity_provider_id == self.connection.id,
        )
        if desired:
            stmt = stmt.where(UserRole.role_id.not_in(desired))
        removed = set(await self.session.scalars(stmt))
        if removed:
            await self.session.execute(
                delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id.in_(removed))
            )
            await self.session.flush()
        return removed

    async def _set_active(self, user: User, active: bool) -> None:
        if user.is_active == active:
            return
        if not active:
            from shared.account_guards import (
                AccountGuardError,
                refuse_last_active_platform_admin_removal,
            )
            from shared.human_sessions import revoke_all_human_sessions_in_transaction

            try:
                await refuse_last_active_platform_admin_removal(self.session, {user.id})
            except AccountGuardError as exc:
                raise ProvisioningError(ProvisioningErrorKind.CONFLICT, exc.detail) from None
            user.is_active = False
            await self.session.flush()
            await revoke_all_human_sessions_in_transaction(self.session, user.id)
        else:
            user.is_active = True
            await self.session.flush()

    async def _invalidate_role(self, role_id: UUID) -> None:
        from src.core.cache import invalidate_role_users

        await invalidate_role_users(None, str(role_id))

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    async def _require_claimed_domain(self, email: str) -> None:
        domain = email_domain(email)
        if domain is None or domain not in await verified_domains(
            self.session, self.connection.organization_id
        ):
            raise ProvisioningError(
                ProvisioningErrorKind.DOMAIN_NOT_CLAIMED,
                f"The email domain '{domain}' is not a verified domain of this organization",
            )

    async def _require_adoptable(self, user: User) -> None:
        if user.organization_id != self.connection.organization_id:
            raise ProvisioningError(
                ProvisioningErrorKind.CONFLICT,
                "A user with this email belongs to another organization",
            )
        if user.is_system or user.identity_kind is not None:
            raise ProvisioningError(
                ProvisioningErrorKind.CONFLICT, "This email belongs to a system account"
            )
        linked = await self.session.scalar(
            select(IdentityProviderUser.identity_provider_id).where(
                IdentityProviderUser.user_id == user.id
            )
        )
        if linked is not None:
            raise ProvisioningError(
                ProvisioningErrorKind.CONFLICT,
                "This user is already managed by an identity provider",
            )

    async def _refuse_duplicate_link(
        self, record: UserRecord, *, exclude_link_id: UUID | None
    ) -> None:
        clauses = [IdentityProviderUser.user_name == record.user_name]
        if record.external_id is not None:
            clauses.append(IdentityProviderUser.external_id == record.external_id)
        stmt = select(IdentityProviderUser.id).where(
            IdentityProviderUser.identity_provider_id == self.connection.id, or_(*clauses)
        )
        if exclude_link_id is not None:
            stmt = stmt.where(IdentityProviderUser.id != exclude_link_id)
        if await self.session.scalar(stmt) is not None:
            raise ProvisioningError(
                ProvisioningErrorKind.CONFLICT, "A user with this userName or externalId exists"
            )

    async def _refuse_duplicate_group(
        self, record: GroupRecord, *, exclude_group_id: UUID | None
    ) -> None:
        clauses = [IdentityProviderGroup.display_name == record.display_name]
        if record.external_id is not None:
            clauses.append(IdentityProviderGroup.external_id == record.external_id)
        stmt = select(IdentityProviderGroup.id).where(
            IdentityProviderGroup.identity_provider_id == self.connection.id, or_(*clauses)
        )
        if exclude_group_id is not None:
            stmt = stmt.where(IdentityProviderGroup.id != exclude_group_id)
        if await self.session.scalar(stmt) is not None:
            raise ProvisioningError(
                ProvisioningErrorKind.CONFLICT,
                "A group with this displayName or externalId exists",
            )

    async def _known_links(self, link_ids: set[UUID]) -> set[UUID]:
        if not link_ids:
            return set()
        known = set(
            await self.session.scalars(
                select(IdentityProviderUser.id).where(
                    IdentityProviderUser.identity_provider_id == self.connection.id,
                    IdentityProviderUser.id.in_(link_ids),
                )
            )
        )
        unknown = link_ids - known
        if unknown:
            raise ProvisioningError(
                ProvisioningErrorKind.INVALID,
                f"Unknown group member id(s): {', '.join(sorted(str(u) for u in unknown))}",
            )
        return known

    # ------------------------------------------------------------------
    # Creation, audit and events
    # ------------------------------------------------------------------

    async def _new_user(self, email: str, record: UserRecord) -> User:
        from shared.base_role_defaults import BaseRoleDefaultError, resolve_base_role
        from shared.identities import is_identity_email
        from shared.sdk_users import set_user_base_role

        if is_identity_email(email):
            raise ProvisioningError(ProvisioningErrorKind.INVALID, "This email domain is reserved")
        try:
            base_role = await resolve_base_role(
                self.session, organization_id=self.connection.organization_id
            )
        except BaseRoleDefaultError as exc:
            raise ProvisioningError(ProvisioningErrorKind.INVALID, exc.detail) from None
        now = datetime.now(timezone.utc)
        user = User(
            email=email,
            name=record.display_name,
            hashed_password="",
            # Activation is decided by reconciliation right after linking.
            is_active=False,
            is_superuser=False,
            is_external=self.connection.provision_as_external,
            # The identity provider vouches for the address; the person signs
            # in through SSO, which marks the account registered.
            is_verified=True,
            is_registered=False,
            organization_id=self.connection.organization_id,
            created_at=now,
            updated_at=now,
        )
        await set_user_base_role(self.session, user, base_role.id)
        self.session.add(user)
        await self.session.flush()
        return user

    async def _audit(
        self,
        action: str,
        user: User | None,
        details: dict[str, Any],
        *,
        resource_id: Any = None,
    ) -> None:
        from src.services.audit import emit_audit

        await emit_audit(
            self.session,
            action,
            resource_type="user",
            resource_id=user.id if user is not None else resource_id,
            details={
                **details,
                "identity_provider_id": str(self.connection.id),
                "identity_provider_name": self.connection.name,
            },
            actor_override=self._actor,
        )

    def _user_event_body(
        self, user: User, link: IdentityProviderUser | None
    ) -> dict[str, Any]:
        from src.services.events.builtins import identity_provider_user_body

        return identity_provider_user_body(
            organization_id=self.connection.organization_id,
            identity_provider={
                "id": str(self.connection.id),
                "name": self.connection.name,
                "vendor": self.connection.vendor,
            },
            user={
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "is_active": user.is_active,
                "external_id": link.external_id if link else None,
                "attributes": link.attributes if link else None,
            },
        )

    def _queue_user_event(
        self,
        topic: str,
        user: User,
        link: IdentityProviderUser,
        *,
        adopted: bool | None = None,
    ) -> None:
        body = self._user_event_body(user, link)
        if adopted is not None:
            body["adopted"] = adopted
        self.pending_events.append((topic, body))

    async def emit_pending_events(self) -> None:
        """Send collected platform events. Call only after commit."""
        from src.services.events.builtins import emit_identity_provider_event

        events, self.pending_events = self.pending_events, []
        for topic, body in events:
            await emit_identity_provider_event(
                topic,
                body,
                organization_id=self.connection.organization_id,
                identity_provider_id=self.connection.id,
            )
