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

logger = logging.getLogger(__name__)


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
        solution_id: UUID | None = None,
    ) -> FileMetadata:
        """Upsert file metadata. When `solution_id` is provided (solution-scoped
        write), it is stored in `FileMetadata.solution_id` and `organization_id`
        must be the install's org — NOT the install UUID (C2 fix)."""
        # For solution rows, look up by solution_id + location + path (the
        # unique index uq_file_metadata_solution_location_path).
        if solution_id is not None:
            existing = await self._get_solution_metadata(
                solution_id=solution_id,
                location=location,
                path=path,
            )
        else:
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
                solution_id=solution_id,
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

    async def _get_solution_metadata(
        self,
        *,
        solution_id: UUID,
        location: str,
        path: str,
    ) -> "FileMetadata | None":
        stmt = select(FileMetadata).where(
            FileMetadata.solution_id == solution_id,
            FileMetadata.location == location,
            FileMetadata.path == path,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def delete_metadata(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        solution_id: UUID | None = None,
    ) -> None:
        """Delete a FileMetadata row. When ``solution_id`` is set (solution-scoped
        delete), match by ``(solution_id, location, path)`` — mirroring the write
        path in ``upsert_metadata`` — so the row is found even though
        ``organization_id`` is the install's org UUID, not the install UUID itself.
        Non-solution deletes match by ``(organization_id, location, path)``
        unchanged."""
        if solution_id is not None:
            stmt = delete(FileMetadata).where(
                FileMetadata.solution_id == solution_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        else:
            stmt = delete(FileMetadata).where(
                FileMetadata.organization_id == organization_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        await self.db.execute(stmt)
        await self.db.flush()

    async def get_metadata(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> FileMetadata | None:
        # Restrict to non-solution rows (solution_id IS NULL).  With Task-14's
        # partial-unique indexes a solution row and an org row can share the same
        # (organization_id, location, path), so omitting this filter causes
        # scalar_one_or_none() to raise MultipleResultsFound → 500.  Solution
        # metadata reads use _get_solution_metadata (keyed by solution_id) instead.
        stmt = select(FileMetadata).where(
            FileMetadata.organization_id == organization_id,
            FileMetadata.location == location,
            FileMetadata.path == path,
            FileMetadata.solution_id.is_(None),
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
            doc = policies.model_dump(mode="json", by_alias=True)
            # On first creation, seed a VISIBLE, revocable admin_bypass rule
            # (mirroring Tables) so a platform admin is allowed by policy — not
            # by a hardcoded evaluator bypass. Prepend only when absent, so a
            # later update that drops the rule sticks.
            if seed_admin_bypass and not any(
                rule.get("name") == "admin_bypass" or rule.get("$ref") == "admin_bypass"
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

        existing.policies = policies.model_dump(mode="json", by_alias=True)
        await self.db.flush()
        return existing

    async def list_policies(
        self,
        *,
        organization_id: UUID | None,
        location: str | None = None,
    ) -> list[FilePolicy]:
        stmt = select(FilePolicy).where(
            FilePolicy.organization_id == organization_id,
            FilePolicy.solution_id.is_(None),
        )
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
        solution_id: UUID | None = None,
    ) -> FilePolicy | None:
        """Resolve the governing policy with own-solution → org → global cascade.

        When ``solution_id`` is provided (solution context), the solution's own
        prefix policies are consulted first (longest-prefix within the solution
        tier).  A solution-scoped policy that covers the path wins; if none
        matches, resolution falls through to the existing org → global arm
        unchanged.

        For non-solution callers (``solution_id=None``) the original org →
        global cascade applies with no change.
        """
        # Step 0: solution-own arm — only when a solution context is active.
        if solution_id is not None:
            solution_rows = (
                await self.db.execute(
                    select(FilePolicy).where(
                        FilePolicy.solution_id == solution_id,
                        FilePolicy.location == location,
                    )
                )
            ).scalars().all()
            solution_match = select_longest_prefix(solution_rows, location, path)  # type: ignore[arg-type]
            if solution_match is not None:
                return cast(FilePolicy, solution_match)

        # Step 1: org-specific (override) — longest-prefix among the org's rows.
        if organization_id is not None:
            org_rows = (
                await self.db.execute(
                    select(FilePolicy).where(
                        FilePolicy.organization_id == organization_id,
                        FilePolicy.location == location,
                        FilePolicy.solution_id.is_(None),
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
                    FilePolicy.solution_id.is_(None),
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
        solution_id: UUID | None = None,
    ) -> bool:
        if not self._principal_matches_org(user, organization_id):
            return False

        policy_row = await self.load_policy(
            organization_id=organization_id,
            location=location,
            path=path,
            solution_id=solution_id,
        )
        if policy_row is None:
            return False

        try:
            policies = FilePolicies.model_validate(policy_row.policies)
        except ValidationError as exc:
            logger.warning(
                "malformed file policies; denying file access: %s",
                exc.__class__.__name__,
            )
            return False

        rule_repo = PolicyRuleRepository(self.db, org_id=organization_id, is_superuser=True)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="file")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning(
                "unresolvable file policy ref; denying file access: %s",
                exc.__class__.__name__,
            )
            return False

        await preresolve_for_policies(
            user,
            policies,  # type: ignore[arg-type]
            self.db,
            organization_id,
            solution_id,
        )
        if solution_id is not None:
            metadata = await self._get_solution_metadata(
                solution_id=solution_id,
                location=location,
                path=path,
            )
        else:
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

    async def _get_policy_exact(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
    ) -> FilePolicy | None:
        # Restrict to non-solution rows (solution_id IS NULL).  With Task-14's
        # partial-unique indexes a solution row and an org row can share the same
        # (organization_id, location, path), so omitting this filter causes
        # scalar_one_or_none() to raise MultipleResultsFound → 500.  Callers of
        # _get_policy_exact are all org-management paths (upsert, delete, exact-
        # get); solution-policy resolution uses load_policy's separate solution arm.
        stmt = select(FilePolicy).where(
            FilePolicy.organization_id == organization_id,
            FilePolicy.location == location,
            FilePolicy.path == path,
            FilePolicy.solution_id.is_(None),
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
