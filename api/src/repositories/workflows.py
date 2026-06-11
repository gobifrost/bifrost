"""
Workflow Repository

Database operations for workflow registry.
Replaces scan_all_workflows() with efficient database queries.

The workflows table now stores all executable types:
- 'workflow': Standard workflows (@workflow decorator)
- 'tool': AI agent tools (@tool decorator)
- 'data_provider': Data providers for forms/app builder (@data_provider decorator)

Organization Scoping:
- Workflows with organization_id = NULL are global (available to all orgs)
- Workflows with organization_id set are org-scoped
- Queries filter: global + user's org (unless platform admin requests all)

Access Control:
- Workflows use OrgScopedRepository for cascade scoping
- Role-based access via WorkflowRole junction table
- access_level field: 'authenticated' or 'role_based'
"""

import logging
from datetime import datetime, timezone
from typing import Literal, Sequence
from uuid import UUID

from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import selectinload

from src.core.org_filter import OrgFilterType
from src.models import Workflow
from src.models.orm.workflow_roles import WorkflowRole
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)

# Type discriminator values
WorkflowType = Literal["workflow", "tool", "data_provider"]


class WorkflowRepository(OrgScopedRepository[Workflow]):
    """
    Repository for workflow registry operations.

    Uses OrgScopedRepository for cascade scoping:
    - Org users see: org-specific workflows + global (NULL org_id) workflows
    - Role-based access: workflows with access_level="role_based" require role assignment

    Class attributes:
        model: Workflow ORM model
        role_table: WorkflowRole junction table for RBAC
        role_entity_id_column: Column name linking roles to workflows
    """

    model = Workflow
    role_table = WorkflowRole
    role_entity_id_column = "workflow_id"

    # ==========================================================================
    # Identifier Resolution
    # ==========================================================================

    async def resolve(
        self, identifier: str, *, solution_scope: UUID | None = None
    ) -> Workflow | None:
        """Resolve a workflow by UUID or path::function_name.

        Resolution order:
        1. If identifier parses as a UUID, look up by id.
        2. If identifier contains '::', treat as path::function_name lookup.

        Bare names are not supported because workflow names are not unique
        (no DB constraint). Use UUID or path::function_name instead.

        Args:
            identifier: A workflow UUID string or "path::function_name" ref
            solution_scope: The CALLING install's solution_id, when the caller is
                a Solution app/form. A v2 app's ``path::fn`` ref carries no
                install id (it can't know the per-install uuid5), so path-ref
                resolution disambiguates to THIS install's own workflow first,
                falling back to the global ``_repo/`` row. Without it, two
                same-org installs sharing a path would resolve non-deterministically
                (Codex #8 P1). Ignored for UUID lookups (already unambiguous).

        Returns:
            Workflow if found and accessible, None otherwise
        """
        try:
            workflow_uuid = UUID(identifier)
            return await self.get(id=workflow_uuid)
        except ValueError:
            pass

        # path::function_name format (portable ref used by app code)
        if "::" in identifier:
            return await self._resolve_by_path_ref(identifier, solution_scope=solution_scope)

        return None

    async def _resolve_by_path_ref(
        self, ref: str, *, solution_scope: UUID | None = None
    ) -> Workflow | None:
        """Resolve a path::function_name reference to a workflow.

        Args:
            ref: A string like "workflows/foo.py::bar" or "path::function_name"
            solution_scope: see :meth:`resolve`.

        Returns:
            Workflow if found, None otherwise
        """
        path, _, function_name = ref.rpartition("::")
        if not path or not function_name:
            return None

        stmt = (
            select(Workflow)
            .where(
                Workflow.path == path,
                Workflow.function_name == function_name,
                Workflow.is_active.is_(True),
            )
        )
        # Include solution-managed rows: a v2 Solution app (and forms) reference
        # their own workflow by ``path::fn`` — it cannot hard-code the per-install
        # uuid5 id it won't know until install (R7-P1-c). Cascade scope limits to
        # (caller org OR global); the disambiguation below picks the right row.
        stmt = self._apply_cascade_scope(stmt)
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return None
        # A path can match a _repo/ row and/or one-or-more solution rows (two
        # different solution slugs in one org both shipping workflows/main.py).
        # Disambiguate DETERMINISTICALLY by the CALLER'S scope (Codex #8/#11).
        # This MUST run even for a single row, so a scoped caller never resolves
        # a lone SIBLING-install row by the count==1 shortcut.
        if solution_scope is not None:
            # A Solution app/form caller: resolve THIS install's own workflow
            # first (no guesswork), else the global _repo/ row (the app
            # referenced a shared-library path). NEVER a SIBLING install's row —
            # a stale/typo'd ref in app A must not execute app B's workflow just
            # because they share a path. Absent both → None (the caller 404s).
            own = [w for w in rows if w.solution_id == solution_scope]
            if own:
                return own[0]
            repo_rows = [w for w in rows if w.solution_id is None]
            return repo_rows[0] if repo_rows else None
        # No install scope — a _repo/ or system caller. Prefer the _repo/ row so a
        # shared-library path-ref is never hijacked by a Solution reusing the
        # path; fall back to a solution row only when there is no _repo/ row AND
        # exactly one solution row is visible (nothing to guess between).
        repo_rows = [w for w in rows if w.solution_id is None]
        if repo_rows:
            return repo_rows[0]
        if len(rows) == 1:
            return rows[0]
        # 2+ solution rows, no _repo/ row, no caller scope: never guess which
        # install executes — callers 404 with the ref in the detail.
        logger.warning(
            "Ambiguous unscoped path-ref %s::%s matches %d solution workflows; refusing",
            path,
            function_name,
            len(rows),
        )
        return None

    # ==========================================================================
    # Type-Based Queries
    # ==========================================================================

    async def get_by_type(
        self,
        type: WorkflowType,
        active_only: bool = True,
    ) -> Sequence[Workflow]:
        """Get workflows filtered by type with cascade scoping.

        Uses the repository's org_id for cascade scoping (org + global).

        Args:
            type: The type to filter by ('workflow', 'tool', 'data_provider')
            active_only: If True, only return active workflows

        Returns:
            Sequence of workflows matching the type
        """
        stmt = select(Workflow).where(Workflow.type == type)
        if active_only:
            stmt = stmt.where(Workflow.is_active.is_(True))
        stmt = self._apply_cascade_scope(stmt)
        stmt = stmt.order_by(Workflow.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_data_providers(
        self,
        active_only: bool = True,
    ) -> Sequence[Workflow]:
        """Get all data providers.

        Convenience method for get_by_type('data_provider').
        """
        return await self.get_by_type("data_provider", active_only=active_only)

    async def get_tools(
        self,
        active_only: bool = True,
    ) -> Sequence[Workflow]:
        """Get all AI agent tools.

        Convenience method for get_by_type('tool').
        """
        return await self.get_by_type("tool", active_only=active_only)

    async def list_tools_for_filter(
        self,
        filter_type: OrgFilterType,
        filter_org_id: UUID | None,
        active_only: bool = True,
    ) -> Sequence[Workflow]:
        """List workflow tools for an already-resolved organization filter."""
        stmt = (
            select(Workflow)
            .where(Workflow.type == "tool")
            .options(selectinload(Workflow.organization))
        )
        if active_only:
            stmt = stmt.where(Workflow.is_active.is_(True))

        if filter_type is OrgFilterType.ORG_PLUS_GLOBAL:
            if filter_org_id is None:
                stmt = stmt.where(Workflow.organization_id.is_(None))
            else:
                stmt = stmt.where(
                    or_(
                        Workflow.organization_id == filter_org_id,
                        Workflow.organization_id.is_(None),
                    )
                )
        elif filter_type is OrgFilterType.ORG_ONLY:
            # A None org here is not a license to read global (``== None``
            # compiles to ``IS NULL``) — it means no rows.
            if filter_org_id is None:
                stmt = stmt.where(false())
            else:
                stmt = stmt.where(Workflow.organization_id == filter_org_id)
        elif filter_type is OrgFilterType.GLOBAL_ONLY:
            stmt = stmt.where(Workflow.organization_id.is_(None))

        stmt = stmt.order_by(Workflow.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_workflows_only(
        self,
        active_only: bool = True,
    ) -> Sequence[Workflow]:
        """Get only workflows (excludes tools and data providers).

        Convenience method for get_by_type('workflow').
        """
        return await self.get_by_type("workflow", active_only=active_only)

    # ==========================================================================
    # Standard Queries
    # ==========================================================================

    async def get_by_name(self, name: str) -> Workflow | None:
        """Get workflow by name with cascade scoping and role check.

        Uses the base class get() method which handles:
        - Priority: org-specific > global (avoids MultipleResultsFound)
        - Role-based access control

        Args:
            name: Workflow name to look up

        Returns:
            Workflow if found and accessible, None otherwise
        """
        return await self.get(name=name)

    async def get_by_name_and_type(
        self,
        name: str,
        type: WorkflowType,
        active_only: bool = True,
    ) -> Workflow | None:
        """Get workflow by name and type.

        Note: Does not apply cascade scoping - searches all workflows.
        Used for system-level lookups where org context is not relevant.

        Args:
            name: Workflow name to look up
            type: Type filter ('workflow', 'tool', 'data_provider')
            active_only: If True, only return active workflows

        Returns:
            Workflow if found, None otherwise
        """
        stmt = select(Workflow).where(
            Workflow.name == name,
            Workflow.type == type,
            # _repo/-tier lookup: solution workflows are resolved by id, and may
            # reuse a _repo/ name — exclude them to avoid MultipleResultsFound.
            Workflow.solution_id.is_(None),
        )
        if active_only:
            stmt = stmt.where(Workflow.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_active(self) -> Sequence[Workflow]:
        """Get all active workflows with cascade scoping.

        Uses the repository's org_id for cascade scoping (org + global).

        Returns:
            Sequence of active workflows in scope
        """
        stmt = select(Workflow).where(Workflow.is_active.is_(True))
        stmt = self._apply_cascade_scope(stmt)
        stmt = stmt.order_by(Workflow.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_endpoint_enabled(self) -> Sequence[Workflow]:
        """Get all active workflows with endpoint enabled.

        Note: Returns workflows across all organizations (system-level access).
        Endpoint routing needs visibility of all endpoint-enabled workflows.
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.is_active.is_(True))
            .where(Workflow.endpoint_enabled.is_(True))
            .order_by(Workflow.name)
        )
        return result.scalars().all()

    async def get_by_category(self, category: str) -> Sequence[Workflow]:
        """Get all active workflows in a category.

        Note: Returns workflows across all organizations (system-level access).
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.is_active.is_(True))
            .where(Workflow.category == category)
            .order_by(Workflow.name)
        )
        return result.scalars().all()

    async def count_active(self) -> int:
        """Count all active workflows."""
        result = await self.session.execute(
            select(func.count(Workflow.id))
            .where(Workflow.is_active.is_(True))
        )
        return result.scalar() or 0

    async def search(
        self,
        query: str | None = None,
        category: str | None = None,
        type: WorkflowType | None = None,
        endpoint_enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Workflow]:
        """Search workflows with filters and cascade scoping.

        Uses the repository's org_id for cascade scoping (org + global).

        Args:
            query: Text search in name/description
            category: Filter by category
            type: Filter by type ('workflow', 'tool', 'data_provider')
            endpoint_enabled: Filter by endpoint_enabled flag
            limit: Maximum number of results
            offset: Result offset for pagination

        Returns:
            Sequence of matching workflows
        """
        stmt = select(Workflow).where(Workflow.is_active.is_(True))

        # Apply cascade scoping
        stmt = self._apply_cascade_scope(stmt)

        if query:
            stmt = stmt.where(
                Workflow.name.ilike(f"%{query}%") |
                Workflow.description.ilike(f"%{query}%")
            )

        if category:
            stmt = stmt.where(Workflow.category == category)

        if type:
            stmt = stmt.where(Workflow.type == type)

        if endpoint_enabled is not None:
            stmt = stmt.where(Workflow.endpoint_enabled == endpoint_enabled)

        stmt = stmt.order_by(Workflow.name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ==========================================================================
    # API Key Operations
    # ==========================================================================

    async def get_by_api_key_hash(self, key_hash: str) -> Workflow | None:
        """Get workflow by API key hash.

        Note: Returns workflow regardless of organization (system-level access).
        API key authentication bypasses org scoping by design.
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.api_key_hash == key_hash)
            .where(Workflow.api_key_enabled.is_(True))
            .where(Workflow.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def set_api_key(
        self,
        workflow_id: UUID,
        key_hash: str,
        description: str | None,
        created_by: str,
        expires_at: datetime | None = None,
    ) -> Workflow | None:
        """Set API key for a workflow."""
        workflow = await self.get(id=workflow_id)
        if not workflow:
            return None

        workflow.api_key_hash = key_hash
        workflow.api_key_description = description
        workflow.api_key_enabled = True
        workflow.api_key_created_by = created_by
        workflow.api_key_created_at = datetime.now(timezone.utc)
        workflow.api_key_expires_at = expires_at
        workflow.api_key_last_used_at = None

        await self.session.flush()
        return workflow

    async def revoke_api_key(self, workflow_id: UUID) -> Workflow | None:
        """Revoke API key for a workflow."""
        workflow = await self.get(id=workflow_id)
        if not workflow:
            return None

        workflow.api_key_enabled = False
        await self.session.flush()
        return workflow

    async def update_api_key_last_used(self, workflow_id: UUID) -> None:
        """Update last used timestamp for API key."""
        workflow = await self.get(id=workflow_id)
        if workflow:
            workflow.api_key_last_used_at = datetime.now(timezone.utc)
            await self.session.flush()

    async def get_endpoint_workflow_by_id(self, workflow_id: UUID) -> Workflow | None:
        """
        Get endpoint-enabled workflow by UUID.

        Used by the /api/endpoints/{workflow_id} route.

        Args:
            workflow_id: Workflow UUID

        Returns:
            Workflow if found, active, and endpoint-enabled; None otherwise
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .where(Workflow.endpoint_enabled.is_(True))
            .where(Workflow.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def get_endpoint_workflow_by_name(self, name: str) -> Workflow | None:
        """
        Get endpoint-enabled workflow by name.

        Used by the /api/endpoints/{workflow_name} route to resolve
        user-friendly names to workflow IDs.

        Args:
            name: Workflow name to look up

        Returns:
            Workflow if exactly one found, None if not found

        Raises:
            ValueError: If multiple endpoint-enabled workflows have the same name
                        (includes file paths for debugging)
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.name == name)
            .where(Workflow.endpoint_enabled.is_(True))
            .where(Workflow.is_active.is_(True))
        )
        workflows = list(result.scalars().all())

        if len(workflows) == 0:
            return None

        if len(workflows) > 1:
            paths = [w.path for w in workflows]
            raise ValueError(
                f"Multiple endpoint-enabled workflows named '{name}' found: {paths}"
            )

        return workflows[0]

    async def validate_api_key(
        self,
        key_hash: str,
        workflow_id: UUID | None = None,
    ) -> tuple[bool, UUID | None]:
        """
        Validate an API key.

        Args:
            key_hash: SHA-256 hash of the API key
            workflow_id: If provided, validates key works for this workflow

        Returns:
            Tuple of (is_valid, workflow_id)
        """
        # Check for workflow-specific key
        stmt = (
            select(Workflow)
            .where(Workflow.api_key_hash == key_hash)
            .where(Workflow.api_key_enabled.is_(True))
            .where(Workflow.is_active.is_(True))
        )

        if workflow_id:
            stmt = stmt.where(Workflow.id == workflow_id)

        result = await self.session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if workflow:
            # Check expiration
            if workflow.api_key_expires_at and workflow.api_key_expires_at < datetime.now(timezone.utc):
                return False, None

            return True, workflow.id

        return False, None
