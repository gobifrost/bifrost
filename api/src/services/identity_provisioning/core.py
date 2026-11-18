"""Protocol-independent provisioning commands for one identity provider
connection.

Every command runs inside the caller's transaction. The rules it enforces:

- A connection only creates or changes users whose email domain is a
  verified claim of its organization, and never touches a user whose home is
  another organization.
- An existing user in the same organization is adopted by email; one in
  another organization is a conflict. Emails and userNames match without
  regard to case.
- Privileged access is decided in Bifrost, never by a directory: a
  privileged user is never adopted, a role carrying privileged permissions
  is never granted, and a linked user who becomes privileged is revoke-only
  (the directory can still deactivate or unlink them and remove roles it
  granted, but not re-activate, rename, re-email or grant).
- A connection acts only on users in its organization; one that has left
  it is left alone.
- The identity provider owns name, email and active for linked users.
- Roles come from group mappings. Provisioning only removes the role
  assignments it created (``UserRole.identity_provider_id``), so a manual
  grant always survives.
- Once a connection has at least one group mapping, a user needs at least one
  mapped group to stay active.
- Deleting a user disables and unlinks them, except a user this connection
  created who has never signed in: that account has no history to keep and
  is removed permanently.
- Revoke now, grant later: deactivation and role removal apply in the
  request; activation, new roles and renames wait for the access pass
  (``access_pass``), which applies a whole burst in one transaction. Each of
  those writes changes the global authority epoch.

Locking: every command takes the connection's policy lock (shared; a mapping
change takes it exclusively) before any user row lock, and locks users in id
order before reading the links and memberships it decides from.

Audit rows are written in the transaction; platform events and cache
invalidations are collected and run by :meth:`Provisioner.after_commit` once
the caller commits.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text
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
from src.models.orm.users import Role, User, UserRole
from src.services.audit_context import ActorContext
from src.services.identity_provisioning.domains import email_domain, verified_domains

logger = logging.getLogger(__name__)

# Roles no group mapping may ever grant: base roles are held through
# ``User.base_role_id``, and the platform-wide builtin roles are assigned by a
# person, never by a directory.
UNMAPPABLE_ROLE_IDS = frozenset(
    {*BASE_ROLE_IDS, PLATFORM_ADMIN_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID}
)

PRIVILEGED_USER_MESSAGE = (
    "This user holds privileged access, which is managed in Bifrost, not by an identity provider"
)
PRIVILEGED_ROLE_MESSAGE = "Roles with privileged permissions aren't granted by identity providers"
OTHER_ORG_USER_MESSAGE = "This user no longer belongs to this connection's organization"


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
    # None when the directory didn't say: a new user starts active, an
    # existing one keeps what it had.
    active: bool | None
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
        # Cache entries to drop once the transaction commits.
        self.stale_roles: set[UUID] = set()
        self.stale_users: set[UUID] = set()
        self._policy_lock: str | None = None
        self._actor = ActorContext(
            user_id=None,
            organization_id=connection.organization_id,
            email=None,
            name=f"Identity provider: {connection.name}",
            source="identity_provider",
            surface="service",
        )

    async def hold_policy(self, *, exclusive: bool = False) -> None:
        """Take the connection's policy lock for this transaction.

        Every provisioning command decides access from the connection's group
        mappings, and a mapping change recomputes every linked user. Commands
        share the lock and a mapping change takes it exclusively, so a user
        created while the first mapping is added can't be missed by either.
        Take it before any user row lock; every path keeps that order. Decide
        the mode up front: two transactions upgrading shared to exclusive
        would deadlock, so an upgrade is refused.
        """
        if self._policy_lock == "exclusive" or (self._policy_lock == "shared" and not exclusive):
            return
        if self._policy_lock == "shared":
            raise RuntimeError("The policy lock is held shared; take it exclusively from the start")
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        await self.session.execute(
            text(f"SELECT {function}(hashtextextended(:key, 0))"),
            {"key": f"identity_provider_policy:{self.connection.id}"},
        )
        self._policy_lock = "exclusive" if exclusive else "shared"

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

    async def lock_user_link(self, link_id: UUID) -> IdentityProviderUser | None:
        """The link, read after taking the policy lock and its user's row
        lock, so a change computed from it (a PATCH) can't overwrite a
        concurrent one."""
        await self.hold_policy()
        user_id = await self.session.scalar(
            select(IdentityProviderUser.user_id).where(
                IdentityProviderUser.id == link_id,
                IdentityProviderUser.identity_provider_id == self.connection.id,
            )
        )
        if user_id is None:
            return None
        await self._lock_user(user_id)
        return await self.session.scalar(
            select(IdentityProviderUser)
            .where(IdentityProviderUser.id == link_id)
            .execution_options(populate_existing=True)
        )

    async def lock_group(
        self, group_id: UUID, *, exclusive_policy: bool = False
    ) -> IdentityProviderGroup | None:
        """The group, read after taking the policy lock and its row lock, so
        a membership change computed from it can't overwrite a concurrent
        one. Deleting a group changes policy: pass ``exclusive_policy``."""
        await self.hold_policy(exclusive=exclusive_policy)
        return await self.session.scalar(
            select(IdentityProviderGroup)
            .where(
                IdentityProviderGroup.id == group_id,
                IdentityProviderGroup.identity_provider_id == self.connection.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
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
        await self.hold_policy()
        await self._refuse_duplicate_link(record, exclude_link_id=None)
        email = normalize_email(record.email)
        await self._require_claimed_domain(email)

        # Lock a user being adopted before deciding it may be.
        matches = (
            await self.session.scalars(
                select(User)
                .where(func.lower(User.email) == email)
                .order_by(User.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        if len(matches) > 1:
            # Accounts that differ only in letter case: adopting either
            # would be a guess.
            raise ProvisioningError(
                ProvisioningErrorKind.CONFLICT,
                "Several accounts share this email in different letter cases; "
                "resolve them in Bifrost first",
            )
        user = matches[0] if matches else None
        created_user = user is None
        # Unsaid, a new user starts active and an adopted one keeps its state.
        link_active = record.active if record.active is not None else (user is None or user.is_active)
        if user is None:
            # Insert in the final state: a new user carries no token, so the
            # insert leaves the authority epoch alone where a later flip of
            # is_active would not. A new user has no groups yet, so once the
            # connection maps any group, the user waits for one.
            active = link_active and not await self._mapped_group_roles()
            user = await self._new_user(email, record, active=active)
        else:
            await self._require_adoptable(user)

        link = IdentityProviderUser(
            identity_provider_id=self.connection.id,
            user_id=user.id,
            external_id=record.external_id,
            user_name=record.user_name,
            display_name=record.display_name,
            active=link_active,
            attributes=record.attributes,
            created_user=created_user,
        )
        self.session.add(link)
        await self.session.flush()
        await self._reconcile_access([link.id], defer_grants=True)
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
        await self.hold_policy()
        await self._refuse_duplicate_link(record, exclude_link_id=link.id)
        user = await self._lock_user(link.user_id)
        revoke_only = await self._revoke_only(user)
        email = normalize_email(record.email)
        if email != user.email.lower():
            if revoke_only:
                raise ProvisioningError(ProvisioningErrorKind.CONFLICT, PRIVILEGED_USER_MESSAGE)
            await self._require_claimed_domain(email)
            taken = await self.session.scalar(
                select(User.id).where(func.lower(User.email) == email, User.id != user.id)
            )
            if taken is not None:
                raise ProvisioningError(
                    ProvisioningErrorKind.CONFLICT, "Another user already has this email"
                )
            user.email = email
        link.external_id = record.external_id
        link.user_name = record.user_name
        link.display_name = record.display_name
        if record.active is not None:
            link.active = record.active
        link.attributes = record.attributes
        await self.session.flush()
        await self._reconcile_access([link.id], defer_grants=True)
        await self._audit("identity_provider.user_updated", user, {"email": user.email})
        self._queue_user_event("user.updated", user, link)
        return link

    async def delete_user(self, link: IdentityProviderUser) -> None:
        await self.hold_policy()
        user = await self._lock_user(link.user_id)
        # Removing access is always the directory's call, privileged or not.
        await self._revoke_only(user)
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
        self._mark_stale(user.id, removed)
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
        await self.hold_policy()
        # Two membership changes to one group serialize on the group row, so
        # neither computes its difference from a stale member list.
        await self.session.execute(
            select(IdentityProviderGroup.id)
            .where(IdentityProviderGroup.id == group.id)
            .with_for_update()
        )
        requested = set(member_link_ids)
        current = await self.group_member_ids(group.id)
        wanted = await self._known_links(requested)
        # Lock the affected users before touching membership rows, the order
        # delete_user follows (user, then the link and its memberships), so
        # the two can't deadlock. Then read again: a deletion that held one
        # of those locks has committed by now, and a member it removed is
        # refused as unknown rather than written.
        await self._lock_users_of(wanted ^ current)
        wanted = await self._known_links(requested)
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
            await self._reconcile_access(
                added | removed,
                defer_grants=True,
                group_changes={
                    link_id: ("added" if link_id in added else "removed")
                    for link_id in added | removed
                },
                group=group,
            )

    async def delete_group(self, group: IdentityProviderGroup) -> None:
        # Deleting a mapped group deletes its mapping: a policy change.
        await self.hold_policy(exclusive=True)
        affected = await self.group_member_ids(group.id)
        mapped = await self._mapped_group_roles()
        if mapped.keys() == {group.id}:
            # Deleting the last mapped group lifts the mapped-group
            # requirement from every linked user, members or not.
            affected = set(
                await self.session.scalars(
                    select(IdentityProviderUser.id).where(
                        IdentityProviderUser.identity_provider_id == self.connection.id
                    )
                )
            )
        await self.session.delete(group)
        await self.session.flush()
        await self._reconcile_access(affected, defer_grants=True)

    # ------------------------------------------------------------------
    # Access reconciliation
    # ------------------------------------------------------------------

    async def reconcile_all(self) -> None:
        """Recompute and apply access for every linked user, grants included:
        an administrator changed a mapping and expects it to take effect.
        The caller took the policy lock exclusively before the change."""
        assert self._policy_lock == "exclusive", "take hold_policy(exclusive=True) first"
        link_ids = await self.session.scalars(
            select(IdentityProviderUser.id).where(
                IdentityProviderUser.identity_provider_id == self.connection.id
            )
        )
        await self._reconcile_access(set(link_ids), defer_grants=False)

    async def apply_pending(self, link_ids: Iterable[UUID]) -> None:
        """Apply the grants these links wait for (the access pass)."""
        await self._reconcile_access(link_ids, defer_grants=False)

    async def _reconcile_access(
        self,
        link_ids: Iterable[UUID],
        *,
        defer_grants: bool,
        group_changes: dict[UUID, str] | None = None,
        group: IdentityProviderGroup | None = None,
    ) -> None:
        """Bring each linked user's activity, mapped roles and name in line
        with the directory.

        Revocations (deactivating, removing roles this connection granted)
        always apply now. With ``defer_grants``, grants (activating, adding a
        role, renaming) are recorded on the link for the access pass instead
        (``access_pass``). Each of those writes changes the global authority
        epoch, which retires every access token on the instance; the pass
        applies a whole provisioning burst in one transaction.
        """
        link_ids = set(link_ids)
        if not link_ids:
            return
        await self.hold_policy()
        mapped_roles = await self._mapped_group_roles()
        has_mappings = bool(mapped_roles)
        # Lock users in id order so a change touching many users can't
        # deadlock with another, then read everything the decision rests on
        # (links, memberships, assignments) fresh: a writer that held a lock
        # has committed by now, and READ COMMITTED shows its work.
        user_ids = set(
            await self.session.scalars(
                select(IdentityProviderUser.user_id).where(IdentityProviderUser.id.in_(link_ids))
            )
        )
        users = {
            user.id: user
            for user in await self.session.scalars(
                select(User)
                .where(User.id.in_(user_ids))
                .order_by(User.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        links = (
            await self.session.scalars(
                select(IdentityProviderUser)
                .where(IdentityProviderUser.id.in_(link_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
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

        unmanaged = await self._unmanaged(users.values())

        def queue_group_event(user: User, link: IdentityProviderUser) -> None:
            # Built once the user's access is settled, so it reports the
            # user as the change leaves them.
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
                        },
                    )
                )

        now = datetime.now(timezone.utc)
        for link in sorted(links, key=lambda link: link.user_id):
            user = users[link.user_id]
            reason = unmanaged.get(user.id)
            if reason == OTHER_ORG_USER_MESSAGE:
                # Membership is still recorded, but this connection decides
                # nothing about a user outside its organization.
                link.access_pending_since = None
                queue_group_event(user, link)
                continue
            # A privileged user's access only ever shrinks from here.
            revoke_only = reason is not None
            mapped = memberships.get(link.id, set()) & mapped_roles.keys()
            desired_roles: set[UUID] = set()
            for group_id in mapped:
                desired_roles |= mapped_roles[group_id]
            desired_active = link.active and (not has_mappings or bool(mapped))
            desired_name = link.display_name or user.name

            change = _AccessChange()
            if user.is_active and not desired_active:
                await self._set_active(user, False)
                change.deactivated = True
            change.roles_removed = await self._remove_granted_roles(user.id, desired_roles)
            if revoke_only:
                link.access_pending_since = None
            elif defer_grants:
                waiting = (
                    (desired_active and not user.is_active)
                    or bool(await self._missing_roles(user, desired_roles))
                    or user.name != desired_name
                )
                link.access_pending_since = (link.access_pending_since or now) if waiting else None
            else:
                if desired_active and not user.is_active:
                    await self._set_active(user, True)
                    change.activated = True
                change.roles_added = await self._grant_roles(user, desired_roles)
                if user.name != desired_name:
                    user.name = desired_name
                link.access_pending_since = None
            await self.session.flush()

            self._mark_stale(user.id, change.roles_added | change.roles_removed)
            queue_group_event(user, link)
            if change.activated or change.roles_added or change.roles_removed:
                self.pending_events.append(
                    (
                        "user.access_changed",
                        {
                            **self._user_event_body(user, link),
                            "access": {
                                "activated": change.activated,
                                "roles_added": sorted(str(r) for r in change.roles_added),
                                "roles_removed": sorted(str(r) for r in change.roles_removed),
                            },
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

    async def _missing_roles(self, user: User, desired: set[UUID]) -> set[UUID]:
        """Desired roles the user doesn't hold yet."""
        held = set(
            await self.session.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id))
        )
        return desired - held - {user.base_role_id}

    async def _grant_roles(self, user: User, desired: set[UUID]) -> set[UUID]:
        from shared.account_guards import AccountGuardError
        from src.services.authorization.privilege import is_privileged_principal
        from src.services.user_role_assignments import (
            RoleAssignmentError,
            default_boundaries,
            insert_assignment,
            load_roles,
        )

        added: set[UUID] = set()
        missing = await self._missing_roles(user, desired)
        # A mapped role checked at mapping time can gain privileged
        # permissions later, so check again at every grant, under the lock
        # a permission edit waits on.
        await self._lock_roles_shared(missing)
        roles = await load_roles(self.session, missing)
        for role_id in sorted(missing):
            if role_id not in roles:
                # Deleted since the mapping was read: nothing to grant.
                continue
            if is_privileged_principal(roles[role_id].permissions):
                await self._refuse_grant(user, role_id, PRIVILEGED_ROLE_MESSAGE)
                continue
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
                # A policy refusal, e.g. a role an External user can't hold.
                await self._refuse_grant(user, role_id, exc.detail)
                continue
            added.add(role_id)
        return added

    async def _refuse_grant(self, user: User, role_id: UUID, detail: str) -> None:
        """Record a refused grant. Retrying can't change a policy refusal,
        so provisioning skips it and carries on; the next mapping or
        directory change tries again."""
        logger.warning(
            "Identity provider %s skipped role %s for user %s: %s",
            self.connection.id,
            role_id,
            user.id,
            log_safe(detail),
        )
        await self._audit(
            "identity_provider.role_grant_refused",
            user,
            {"email": user.email, "role_id": str(role_id), "reason": detail},
        )

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

    async def _lock_user(self, user_id: UUID) -> User:
        user = await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert user is not None
        return user

    async def _lock_users_of(self, link_ids: set[UUID]) -> None:
        """Lock the users behind these links, in id order."""
        if not link_ids:
            return
        await self.session.execute(
            select(User.id)
            .where(
                User.id.in_(
                    select(IdentityProviderUser.user_id).where(
                        IdentityProviderUser.id.in_(link_ids)
                    )
                )
            )
            .order_by(User.id)
            .with_for_update()
        )

    def _mark_stale(self, user_id: UUID, role_ids: set[UUID]) -> None:
        """Queue cache invalidation for after the commit. Each role's
        invalidation scans Redis, so a burst touching one role scans once."""
        if role_ids:
            self.stale_roles |= role_ids
            self.stale_users.add(user_id)

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
        await self._refuse_unmanaged(user)
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

    async def _lock_roles_shared(self, role_ids: Iterable[UUID]) -> None:
        """Share-lock role rows before reading their permissions. A
        permission edit locks its role for update first, so it waits for
        this decision rather than changing the role under it."""
        ids = set(role_ids)
        if ids:
            await self.session.execute(
                select(Role.id).where(Role.id.in_(ids)).order_by(Role.id).with_for_update(read=True)
            )

    async def _unmanaged(self, users: Iterable[User]) -> dict[UUID, str]:
        """``user_id -> reason`` for the locked ``users`` this connection must
        leave alone: privileged ones, and any no longer in its organization
        (moves are refused, so this only guards the invariant)."""
        from src.services.authorization.enforce import privileged_user_ids

        users = list(users)
        held = set(
            await self.session.scalars(
                select(UserRole.role_id).where(UserRole.user_id.in_([u.id for u in users]))
            )
        )
        await self._lock_roles_shared(held | {u.base_role_id for u in users if u.base_role_id})
        privileged = await privileged_user_ids(self.session, [u.id for u in users])
        unmanaged: dict[UUID, str] = {}
        for user in users:
            if user.organization_id != self.connection.organization_id:
                unmanaged[user.id] = OTHER_ORG_USER_MESSAGE
            elif user.is_superuser or user.id in privileged:
                unmanaged[user.id] = PRIVILEGED_USER_MESSAGE
        return unmanaged

    async def _refuse_unmanaged(self, user: User) -> None:
        reason = (await self._unmanaged([user])).get(user.id)
        if reason is not None:
            raise ProvisioningError(ProvisioningErrorKind.CONFLICT, reason)

    async def _revoke_only(self, user: User) -> bool:
        """Whether the directory may only take access away from this
        linked user (they're privileged); refuses one outside the
        connection's organization."""
        reason = (await self._unmanaged([user])).get(user.id)
        if reason == OTHER_ORG_USER_MESSAGE:
            raise ProvisioningError(ProvisioningErrorKind.CONFLICT, reason)
        return reason is not None

    async def _refuse_duplicate_link(
        self, record: UserRecord, *, exclude_link_id: UUID | None
    ) -> None:
        clauses = [func.lower(IdentityProviderUser.user_name) == record.user_name.lower()]
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

    async def _new_user(self, email: str, record: UserRecord, *, active: bool) -> User:
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
            is_active=active,
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

    async def after_commit(self) -> None:
        """Drop stale cache entries and send collected platform events. Call
        only after the transaction commits, so neither can run ahead of it."""
        from shared.role_cache import invalidate_user
        from src.core.cache import invalidate_role_users
        from src.services.events.builtins import emit_identity_provider_event

        roles, self.stale_roles = self.stale_roles, set()
        users, self.stale_users = self.stale_users, set()
        for role_id in sorted(roles):
            await invalidate_role_users(None, str(role_id))
        for user_id in sorted(users):
            await invalidate_user(user_id)

        events, self.pending_events = self.pending_events, []
        for topic, body in events:
            await emit_identity_provider_event(
                topic,
                body,
                organization_id=self.connection.organization_id,
                identity_provider_id=self.connection.id,
            )
