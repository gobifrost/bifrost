"""
Organization-Scoped Repository

Provides base repository with standardized organization scoping and role-based
access control patterns. All org-scoped repositories should extend this class
for consistent tenant isolation and access control.

Access Control Model:
    - ID lookups (get(id=...)): No cascade needed - IDs are globally unique.
      Find entity directly and check access (superusers bypass role checks).
    - Name/key lookups (get(name=...), get(key=...)): Cascade scoping applies.
      Org-specific first, then global fallback. Respects org_id for correct
      entity resolution even for superusers.
    - Superusers: Skip role checks only, not cascade scoping for name lookups.
    - Regular users: Cascade scoping + role checks (if entity has roles)

Scoping Patterns (for name/key lookups):
    - org_id set: Try org-specific first, then fall back to global
    - org_id None: Only check global scope
"""

from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AccessDeniedError
from src.models import Base, UserRole

ModelT = TypeVar("ModelT", bound=Base)


def _org_filter(model: Any, org_id: UUID | None) -> Any:
    """Filter by organization_id - bypasses type checking for generic model."""
    return model.organization_id == org_id


def _org_is_null(model: Any) -> Any:
    """Check if organization_id is NULL - bypasses type checking for generic model."""
    return model.organization_id.is_(None)


class OrgScopedRepository(Generic[ModelT]):
    """
    Repository with standardized organization scoping and role-based access control.

    This repository combines:
    1. Organization cascade scoping (org-specific + global entities)
    2. Role-based access control (for entities with role tables)

    Subclasses should set:
    - model: The SQLAlchemy model class
    - role_table: (Optional) The role junction table (e.g., FormRole, AppRole)
    - role_entity_id_column: (Optional) The column name for entity ID in role table

    Example usage:
        class FormRepository(OrgScopedRepository[Form]):
            model = Form
            role_table = FormRole
            role_entity_id_column = "form_id"

        class TableRepository(OrgScopedRepository[Table]):
            model = Table
            # No role_table - cascade scoping only

    Access control logic:
        - Superusers: Trust the scope, no role check
        - Regular users: Cascade scoping + role check (if entity has roles)
    """

    model: type[ModelT]
    role_table: type[Base] | None = None
    role_entity_id_column: str = ""

    def __init__(
        self,
        session: AsyncSession,
        org_id: UUID | str | None,
        user_id: UUID | str | None = None,
        is_superuser: bool = False,
    ):
        """
        Initialize repository with database session and access context.

        Args:
            session: SQLAlchemy async session
            org_id: Organization UUID for scoping (None for global-only scope).
                Strings are coerced to UUID — `UUID == str` is False in Python,
                so an unconverted string here silently fails the in-scope check.
            user_id: User UUID for role checks (None for system/superuser).
                Same string-coercion contract as org_id.
            is_superuser: If True, bypasses role checks (trusts scope)
        """
        self.session = session
        self.org_id = UUID(org_id) if isinstance(org_id, str) else org_id
        self.user_id = UUID(user_id) if isinstance(user_id, str) else user_id
        self.is_superuser = is_superuser

    # =========================================================================
    # Public API
    # =========================================================================

    async def get(self, **filters: Any) -> ModelT | None:
        """
        Get a single entity with appropriate scoping based on lookup type.

        Behavior differs based on lookup type:

        **ID lookups (get(id=...)):**
        - IDs are globally unique, no cascade needed
        - Find entity directly and check access
        - Superusers can access any entity by ID

        **Name/key lookups (get(name=...), get(key=...)):**
        - Names can exist in multiple orgs, cascade scoping required
        - Org-specific first, then global fallback
        - Respects org_id even for superusers (correct entity resolution)

        Args:
            **filters: Filter conditions (e.g., id=uuid, name="foo")

        Returns:
            Entity if found and accessible, None otherwise

        Example:
            # Get by ID - no cascade, finds any entity
            entity = await repo.get(id=entity_id)

            # Get by name - cascade: org-specific first, then global
            entity = await repo.get(name="my-entity")
        """
        # Build base query with filters
        query = select(self.model)
        for key, value in filters.items():
            column = getattr(self.model, key, None)
            if column is not None:
                query = query.where(column == value)

        # ID lookup: globally unique, no cascade needed
        # Find the entity directly and check access permissions
        if "id" in filters:
            result = await self.session.execute(query)
            entity = result.scalar_one_or_none()
            if not entity:
                return None

            # Superusers can access any entity by ID
            if self.is_superuser:
                return entity

            # Regular users: verify entity is in their scope (their org or global)
            entity_org_id = getattr(entity, "organization_id", None)
            in_scope = entity_org_id is None or entity_org_id == self.org_id
            if in_scope and await self._can_access_entity(entity):
                return entity
            return None

        # Name/key lookup: cascade scoping applies (even for superusers)
        # This ensures correct entity resolution when names exist in multiple orgs

        # Step 1: Try org-specific lookup (if we have an org)
        if self.org_id is not None:
            org_query = query.where(_org_filter(self.model, self.org_id))
            result = await self.session.execute(org_query)
            entity = result.scalar_one_or_none()
            if entity:
                if self.is_superuser or await self._can_access_entity(entity):
                    return entity
                return None

        # Step 2: Fall back to global
        global_query = query.where(_org_is_null(self.model))
        result = await self.session.execute(global_query)
        entity = result.scalar_one_or_none()

        if entity and (self.is_superuser or await self._can_access_entity(entity)):
            return entity

        return None

    async def can_access(self, **filters: Any) -> ModelT:
        """
        Get entity or raise AccessDeniedError.

        Convenience wrapper around get() that raises an exception if the
        entity is not found or not accessible.

        Args:
            **filters: Filter conditions (e.g., id=uuid, name="foo")

        Returns:
            Entity if found and accessible

        Raises:
            AccessDeniedError: If entity not found or not accessible

        Example:
            try:
                entity = await repo.can_access(id=entity_id)
            except AccessDeniedError:
                raise HTTPException(status_code=403, detail="Access denied")
        """
        entity = await self.get(**filters)
        if not entity:
            raise AccessDeniedError()
        return entity

    async def list(self, **filters: Any) -> list[ModelT]:
        """
        List entities with cascade scoping and role check.

        Returns entities that:
        1. Match the cascade scope (org + global, or global-only)
        2. Pass role-based access check (for entities with role tables)

        Args:
            **filters: Additional filter conditions

        Returns:
            List of accessible entities

        Example:
            # List all accessible entities
            entities = await repo.list()

            # List with additional filters
            entities = await repo.list(is_active=True)
        """
        # Build base query with cascade scoping
        query = select(self.model)
        query = self._apply_cascade_scope(query)

        # Apply additional filters
        for key, value in filters.items():
            column = getattr(self.model, key, None)
            if column is not None:
                query = query.where(column == value)

        result = await self.session.execute(query)
        entities = list(result.scalars().all())

        # Filter by role access (for regular users with role-based entities)
        # NOTE: This does per-entity role checking which may cause N+1 queries
        # for large result sets. Consider batch optimization if performance is an issue.
        if not self.is_superuser and self._has_role_table():
            accessible = []
            for entity in entities:
                if await self._can_access_entity(entity):
                    accessible.append(entity)
            return accessible

        return entities

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _apply_cascade_scope(
        self, query: Select[tuple[ModelT]]
    ) -> Select[tuple[ModelT]]:
        """
        Apply cascade scoping to a query.

        - org_id set: WHERE (organization_id = org_id OR organization_id IS NULL)
        - org_id None: WHERE organization_id IS NULL
        """
        if self.org_id is not None:
            return query.where(
                or_(
                    _org_filter(self.model, self.org_id),
                    _org_is_null(self.model),
                )
            )
        return query.where(_org_is_null(self.model))

    def _has_role_table(self) -> bool:
        """Check if this repository has role-based access control configured."""
        return self.role_table is not None and self.role_entity_id_column != ""

    def _has_access_level(self, entity: ModelT) -> bool:
        """Check if entity has an access_level attribute."""
        return hasattr(entity, "access_level")

    async def _can_access_entity(self, entity: ModelT) -> bool:
        """
        Check if the current user can access an entity.

        Access rules:
        1. Superusers can access anything (scope already filtered)
        2. Entities without access_level: accessible (cascade scoping only)
        3. access_level="authenticated": any user in scope
        4. access_level="role_based": check role membership
        """
        # Superusers bypass role checks
        if self.is_superuser:
            return True

        # No role table configured - cascade scoping only
        if not self._has_role_table():
            return True

        # No access_level attribute - cascade scoping only
        if not self._has_access_level(entity):
            return True

        # Get access level (handle enum or string)
        raw_access_level = getattr(entity, "access_level", None)
        if raw_access_level is None:
            # No access_level set defaults to authenticated
            return True

        # Convert enum to string if needed
        if hasattr(raw_access_level, "value"):
            access_level = raw_access_level.value
        else:
            access_level = str(raw_access_level)

        if access_level == "authenticated":
            return True

        if access_level == "role_based":
            return await self._check_role_access(entity)

        if access_level == "private":
            entity_owner_id = getattr(entity, "owner_user_id", None)
            return entity_owner_id is not None and entity_owner_id == self.user_id

        # Unknown access level - deny
        return False

    async def _check_role_access(self, entity: ModelT) -> bool:
        """
        Check if the current user has a role granting access to this entity.

        Returns True if:
        - User has at least one role that is assigned to the entity
        """
        if self.user_id is None:
            return False

        if not self._has_role_table():
            return True

        # Get user's role IDs
        user_roles_query = select(UserRole.role_id).where(
            UserRole.user_id == self.user_id
        )
        result = await self.session.execute(user_roles_query)
        user_role_ids = list(result.scalars().all())

        if not user_role_ids:
            return False

        # Get entity's role IDs from the role junction table
        role_table = self.role_table
        entity_id_column = getattr(role_table, self.role_entity_id_column, None)
        role_id_column = getattr(role_table, "role_id", None)

        if entity_id_column is None or role_id_column is None:
            return False

        # Access entity.id via getattr (all org-scoped models have an id column)
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            return False

        entity_roles_query = select(role_id_column).where(
            entity_id_column == entity_id
        )
        result = await self.session.execute(entity_roles_query)
        entity_role_ids = list(result.scalars().all())

        # Check intersection
        return any(role_id in entity_role_ids for role_id in user_role_ids)

    # =========================================================================
    # Utility Properties
    # =========================================================================

    @property
    def is_global_scope(self) -> bool:
        """Check if repository is operating in global scope."""
        return self.org_id is None
