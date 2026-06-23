"""Service helpers for file metadata and policy evaluation."""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.claims.preresolve import preresolve_for_policies
from shared.file_policies_seed import make_seed_admin_bypass_file
from shared.file_policies import (
    FilePolicyContext,
    evaluate_file_action,
    select_longest_prefix,
)
from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound, resolve_policy_refs
from src.models.contracts.policies import FileAction, FilePolicies
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.repositories.policy_rule import PolicyRuleRepository
from src.services.audit import emit_audit

logger = logging.getLogger(__name__)


class FilePolicyDenied(PermissionError):
    """Raised by ``check_allowed`` when no file policy grants the action."""


class FilePolicyService:
    """Loads file policy prefixes and evaluates them against file metadata."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_metadata(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        s3_key: str,
        content_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        created_by: UUID | str | None = None,
        updated_by: UUID | str | None = None,
    ) -> FileMetadata:
        existing = await self.get_metadata(
            organization_id=organization_id,
            location=location,
            path=path,
        )
        if existing is None:
            row = FileMetadata(
                organization_id=organization_id,
                location=location,
                path=path,
                s3_key=s3_key,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                created_by=_coerce_uuid(created_by),
                updated_by=_coerce_uuid(updated_by),
            )
            self.db.add(row)
            await self.db.flush()
            return row

        existing.s3_key = s3_key
        existing.content_type = content_type
        existing.size_bytes = size_bytes
        existing.sha256 = sha256
        if existing.created_by is None:
            existing.created_by = _coerce_uuid(created_by)
        existing.updated_by = _coerce_uuid(updated_by)
        await self.db.flush()
        return existing

    async def delete_metadata(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> None:
        await self.db.execute(
            delete(FileMetadata).where(
                FileMetadata.organization_id == organization_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
        await self.db.flush()

    async def get_metadata(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> FileMetadata | None:
        stmt = select(FileMetadata).where(
            FileMetadata.organization_id == organization_id,
            FileMetadata.location == location,
            FileMetadata.path == path,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert_policy(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        policies: FilePolicies,
        created_by: UUID | str | None = None,
        seed_admin_bypass: bool = True,
    ) -> FilePolicy:
        existing = await self._get_policy_exact(
            organization_id=organization_id,
            location=location,
            path=path,
        )
        if existing is None:
            doc = policies.model_dump(mode="json")
            # On first creation, seed a VISIBLE, revocable admin_bypass rule
            # (mirroring Tables) so a platform admin is allowed by policy — not
            # by a hardcoded evaluator bypass. Prepend only when absent, so a
            # later update that drops the rule sticks.
            if seed_admin_bypass and not any(
                rule.get("name") == "admin_bypass"
                for rule in doc.get("policies", [])
            ):
                seed = make_seed_admin_bypass_file()["policies"][0]
                doc["policies"] = [seed, *doc.get("policies", [])]
            row = FilePolicy(
                organization_id=organization_id,
                location=location,
                path=path,
                policies=doc,
                created_by=_coerce_uuid(created_by),
            )
            self.db.add(row)
            await self.db.flush()
            return row

        existing.policies = policies.model_dump(mode="json")
        await self.db.flush()
        return existing

    async def list_policies(
        self,
        *,
        organization_id: UUID | None,
        location: str | None = None,
    ) -> list[FilePolicy]:
        stmt = select(FilePolicy).where(FilePolicy.organization_id == organization_id)
        if location is not None:
            stmt = stmt.where(FilePolicy.location == location)
        stmt = stmt.order_by(FilePolicy.location, FilePolicy.path)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_policy_exact(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> FilePolicy | None:
        return await self._get_policy_exact(
            organization_id=organization_id,
            location=location,
            path=path,
        )

    async def delete_policy(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> bool:
        existing = await self._get_policy_exact(
            organization_id=organization_id,
            location=location,
            path=path,
        )
        if existing is None:
            return False
        await self.db.delete(existing)
        await self.db.flush()
        return True

    async def load_policy(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> FilePolicy | None:
        """Resolve the governing policy with org→global cascade + override.

        Mirrors ``OrgScopedRepository.get`` ("org-specific first, then global"):
        an org-specific policy whose prefix matches the path overrides the
        global (org=NULL) policy, and a global policy applies when the org has
        none — so a global ``shared/pictures`` policy cascades to every org's
        users, and an org's own ``pictures`` policy overrides it for that org.
        The longest-prefix selection runs *within* each scope before the
        override, so org/global specificity is compared independently.
        """
        # Step 1: org-specific (override) — longest-prefix among the org's rows.
        if organization_id is not None:
            org_rows = (
                await self.db.execute(
                    select(FilePolicy).where(
                        FilePolicy.organization_id == organization_id,
                        FilePolicy.location == location,
                    )
                )
            ).scalars().all()
            org_match = select_longest_prefix(org_rows, location, path)  # type: ignore[arg-type]
            if org_match is not None:
                return cast(FilePolicy, org_match)

        # Step 2: fall back to global (org=NULL).
        global_rows = (
            await self.db.execute(
                select(FilePolicy).where(
                    FilePolicy.organization_id.is_(None),
                    FilePolicy.location == location,
                )
            )
        ).scalars().all()
        global_match = select_longest_prefix(global_rows, location, path)  # type: ignore[arg-type]
        return cast("FilePolicy | None", global_match)

    async def is_allowed(
        self,
        action: FileAction,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        user: Any,
    ) -> bool:
        if not self._principal_matches_org(user, organization_id):
            return False

        policy_row = await self.load_policy(
            organization_id=organization_id,
            location=location,
            path=path,
        )
        if policy_row is None:
            return False

        try:
            policies = FilePolicies.model_validate(policy_row.policies)
        except ValidationError as exc:
            logger.warning(
                "malformed file policies for %s/%s/%s; denying: %s",
                organization_id,
                location,
                policy_row.path,
                exc,
            )
            return False

        rule_repo = PolicyRuleRepository(self.db, org_id=organization_id, is_superuser=True)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="file")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning(
                "unresolvable file policy ref %s/%s; denying: %s",
                organization_id,
                location,
                exc,
            )
            return False

        await preresolve_for_policies(
            user,
            policies,  # type: ignore[arg-type]
            self.db,
            organization_id,
        )
        metadata = await self.get_metadata(
            organization_id=organization_id,
            location=location,
            path=path,
        )
        context = FilePolicyContext(
            location=location,
            path=path,
            created_by=metadata.created_by if metadata is not None else None,
            created_at=metadata.created_at if metadata is not None else None,
        )
        return evaluate_file_action(action, policies, context, user)

    async def check_allowed(
        self,
        action: FileAction,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        user: Any,
    ) -> None:
        if await self.is_allowed(
            action,
            organization_id=organization_id,
            location=location,
            path=path,
            user=user,
        ):
            return

        await emit_audit(
            self.db,
            "policy.deny",
            resource_type="file",
            outcome="failure",
            details={
                "policy_action": action,
                "location": location,
                "path": path,
            },
        )
        raise FilePolicyDenied("Access denied")

    async def _get_policy_exact(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> FilePolicy | None:
        stmt = select(FilePolicy).where(
            FilePolicy.organization_id == organization_id,
            FilePolicy.location == location,
            FilePolicy.path == path,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    def _principal_matches_org(
        self,
        user: Any,
        organization_id: UUID | None,
    ) -> bool:
        if organization_id is None:
            return True
        if getattr(user, "is_platform_admin", False):
            return True
        user_org = getattr(user, "organization_id", None)
        return user_org is not None and str(user_org) == str(organization_id)


def _coerce_uuid(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))
