"""Service helpers for file metadata and policy evaluation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, update
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


@dataclass(frozen=True)
class FilePolicyDecision:
    """The single policy evaluation used for enforcement and Access Checks."""

    allowed: bool
    principal: Any
    policies: FilePolicies


@dataclass
class FilePolicyEvalCache:
    """Request-scoped memo for evaluating many paths in one location.

    Used by the ``files.move`` enqueue, which runs up to three policy checks
    per item. It memoizes the enforced policy principal per caller, the policy
    rows each cascade arm loads, each policy row's validated and ref-resolved
    policies, claim pre-resolution per (principal, policy row), and metadata
    rows per path. Decisions are identical to uncached evaluation: the
    canonical cascade and evaluator still run.
    """

    principals: dict[int, tuple[Any, Any]] = field(default_factory=dict)
    policy_rows: dict[tuple[Any, ...], list[FilePolicy]] = field(default_factory=dict)
    resolved: dict[tuple[UUID | None, UUID], tuple[FilePolicies, bool]] = field(
        default_factory=dict
    )
    preresolved: set[tuple[int, UUID]] = field(default_factory=set)
    metadata: dict[tuple[Any, ...], FileMetadata | None] = field(default_factory=dict)


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

    def _scoped_row_filter(
        self,
        *,
        organization_id: UUID | None,
        solution_id: UUID | None,
        location: str,
        path: str,
    ) -> list[Any]:
        """Row key used by the write path (``record_file_write_metadata``)."""
        if solution_id is not None:
            return [
                FileMetadata.solution_id == solution_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            ]
        return [
            FileMetadata.organization_id == organization_id,
            FileMetadata.location == location,
            FileMetadata.path == path,
            FileMetadata.solution_id.is_(None),
        ]

    async def get_scoped_metadata(
        self,
        *,
        organization_id: UUID | None,
        solution_id: UUID | None,
        location: str,
        path: str,
    ) -> FileMetadata | None:
        stmt = select(FileMetadata).where(
            *self._scoped_row_filter(
                organization_id=organization_id,
                solution_id=solution_id,
                location=location,
                path=path,
            )
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def move_metadata(
        self,
        *,
        organization_id: UUID | None,
        solution_id: UUID | None,
        location: str,
        source_row_id: UUID | None,
        destination_path: str,
        s3_key: str,
        content_type: str | None,
        size_bytes: int,
        updated_by: UUID,
    ) -> None:
        """Re-key a moved file's row to its destination; executed, not committed.

        Core statements only: the Solution write guard rejects ORM flushes of
        Solution-owned rows, and every Solution file row is one. A stale row
        already at the destination (its object is gone) is removed first so
        the partial unique indexes hold. The source row keeps its id,
        creator, creation time, size, sha256, and content type.
        """
        await self.db.execute(
            delete(FileMetadata).where(
                *self._scoped_row_filter(
                    organization_id=organization_id,
                    solution_id=solution_id,
                    location=location,
                    path=destination_path,
                )
            )
        )
        if source_row_id is not None:
            await self.db.execute(
                update(FileMetadata)
                .where(FileMetadata.id == source_row_id)
                .values(
                    path=destination_path,
                    s3_key=s3_key,
                    updated_by=updated_by,
                    updated_at=func.now(),
                )
            )
            return
        await self.db.execute(
            insert(FileMetadata).values(
                id=uuid4(),
                organization_id=organization_id,
                solution_id=solution_id,
                location=location,
                path=destination_path,
                s3_key=s3_key,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=None,
                created_by=updated_by,
                updated_by=updated_by,
            )
        )

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
        solution_id: UUID | None = None,
    ) -> list[FilePolicy]:
        """List file policies.

        When ``solution_id`` is set, return that install's solution-tier rows
        (``solution_id == …``) so an admin can SEE the deploy-owned policies —
        writes to them stay blocked (deploy-owned; see the router). Otherwise
        list the org/global workspace tier (``solution_id IS NULL``).
        """
        if solution_id is not None:
            stmt = select(FilePolicy).where(FilePolicy.solution_id == solution_id)
        else:
            stmt = select(FilePolicy).where(
                FilePolicy.organization_id == organization_id,
                FilePolicy.solution_id.is_(None),
            )
        if location is not None:
            stmt = stmt.where(FilePolicy.location == location)
        stmt = stmt.order_by(FilePolicy.location, FilePolicy.path)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_solution_policy_exact(
        self,
        *,
        solution_id: UUID,
        location: str,
        path: str,
    ) -> FilePolicy | None:
        """Fetch one solution-tier policy by natural key (read-only surface)."""
        stmt = select(FilePolicy).where(
            FilePolicy.solution_id == solution_id,
            FilePolicy.location == location,
            FilePolicy.path == path,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

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

    async def _policy_rows(
        self,
        key: tuple[Any, ...],
        stmt: Any,
        cache: "FilePolicyEvalCache | None",
    ) -> list[FilePolicy]:
        if cache is not None and key in cache.policy_rows:
            return cache.policy_rows[key]
        rows = list((await self.db.execute(stmt)).scalars().all())
        if cache is not None:
            cache.policy_rows[key] = rows
        return rows

    async def load_policy(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        solution_id: UUID | None = None,
        cache: "FilePolicyEvalCache | None" = None,
    ) -> FilePolicy | None:
        """Resolve the governing policy with own-solution → org → global cascade.

        When ``solution_id`` is provided (solution context), the solution's own
        prefix policies are consulted first (longest-prefix within the solution
        tier).  A solution-scoped policy that covers the path wins; if none
        matches, resolution falls through to the existing org → global arm
        unchanged.

        For non-solution callers (``solution_id=None``) the original org →
        global cascade applies with no change. ``cache`` memoizes each arm's
        rows for the rest of one request.
        """
        if solution_id is not None:
            solution_rows = await self._policy_rows(
                ("solution", solution_id, location),
                select(FilePolicy).where(
                    FilePolicy.solution_id == solution_id,
                    FilePolicy.location == location,
                ),
                cache,
            )
            solution_match = select_longest_prefix(solution_rows, location, path)  # type: ignore[arg-type]
            if solution_match is not None:
                return cast(FilePolicy, solution_match)

        if organization_id is not None:
            org_rows = await self._policy_rows(
                ("org", organization_id, location),
                select(FilePolicy).where(
                    FilePolicy.organization_id == organization_id,
                    FilePolicy.location == location,
                    FilePolicy.solution_id.is_(None),
                ),
                cache,
            )
            org_match = select_longest_prefix(org_rows, location, path)  # type: ignore[arg-type]
            if org_match is not None:
                return cast(FilePolicy, org_match)

        global_rows = await self._policy_rows(
            ("global", location),
            select(FilePolicy).where(
                FilePolicy.organization_id.is_(None),
                FilePolicy.location == location,
                FilePolicy.solution_id.is_(None),
            ),
            cache,
        )
        global_match = select_longest_prefix(global_rows, location, path)  # type: ignore[arg-type]
        return cast("FilePolicy | None", global_match)

    async def evaluate(
        self,
        action: FileAction,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        user: Any,
        solution_id: UUID | None = None,
        cache: "FilePolicyEvalCache | None" = None,
    ) -> FilePolicyDecision:
        user = await self._enforced_principal(user, cache)
        empty_policies = FilePolicies.model_validate({"policies": []})
        policy_row = await self.load_policy(
            organization_id=organization_id,
            location=location,
            path=path,
            solution_id=solution_id,
            cache=cache,
        )
        if policy_row is None:
            return FilePolicyDecision(False, user, empty_policies)

        resolved_key = (organization_id, policy_row.id)
        if cache is not None and resolved_key in cache.resolved:
            policies, usable = cache.resolved[resolved_key]
        else:
            policies, usable = await self._resolved_policies(policy_row, organization_id)
            if cache is not None:
                cache.resolved[resolved_key] = (policies, usable)
        if not usable:
            return FilePolicyDecision(False, user, policies)

        # Keyed by object identity: the cache holds the principal (see
        # ``_enforced_principal``), so the id cannot be reused while it lives.
        preresolve_key = (id(user), policy_row.id)
        if cache is None or preresolve_key not in cache.preresolved:
            await preresolve_for_policies(
                user,
                policies,  # type: ignore[arg-type]
                self.db,
                organization_id,
                solution_id,
            )
            if cache is not None:
                cache.preresolved.add(preresolve_key)

        metadata_key = (solution_id, organization_id, location, path)
        if cache is not None and metadata_key in cache.metadata:
            metadata = cache.metadata[metadata_key]
        else:
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
            if cache is not None:
                cache.metadata[metadata_key] = metadata
        context = FilePolicyContext(
            location=location,
            path=path,
            created_by=metadata.created_by if metadata is not None else None,
            created_at=metadata.created_at if metadata is not None else None,
        )
        return FilePolicyDecision(
            evaluate_file_action(action, policies, context, user), user, policies
        )

    async def _enforced_principal(
        self, user: Any, cache: "FilePolicyEvalCache | None"
    ) -> Any:
        """The policy principal for ``user``, built once per cache."""
        from src.services.access_check_policies import enforced_policy_principal

        if cache is None:
            return await enforced_policy_principal(self.db, user)
        entry = cache.principals.get(id(user))
        if entry is not None and entry[0] is user:
            return entry[1]
        principal = await enforced_policy_principal(self.db, user)
        cache.principals[id(user)] = (user, principal)
        return principal

    async def _resolved_policies(
        self, policy_row: FilePolicy, organization_id: UUID | None
    ) -> tuple[FilePolicies, bool]:
        """Validated, ref-resolved policies for a row, and whether they can grant.

        A malformed row yields empty policies; an unresolvable ref yields the
        row's policies (for missing-role diagnostics). Both deny.
        """
        try:
            policies = FilePolicies.model_validate(policy_row.policies)
        except ValidationError as exc:
            logger.warning(
                "malformed file policies; denying file access: %s",
                exc.__class__.__name__,
            )
            return FilePolicies.model_validate({"policies": []}), False

        rule_repo = PolicyRuleRepository(self.db, org_id=organization_id)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="file")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning(
                "unresolvable file policy ref; denying file access: %s",
                exc.__class__.__name__,
            )
            return policies, False
        return policies, True

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
        """Compatibility boolean view of :meth:`evaluate`."""
        return (
            await self.evaluate(
                action,
                organization_id=organization_id,
                location=location,
                path=path,
                user=user,
                solution_id=solution_id,
            )
        ).allowed

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

def _coerce_uuid(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))
