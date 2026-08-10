"""
Form Repository

Repository for Form CRUD operations with organization scoping and role-based access.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.org_filter import OrgFilterType
from src.models import Form as FormORM
from src.models.orm.forms import FormRole
from src.repositories.org_scoped import OrgScopedRepository


class FormRepository(OrgScopedRepository[FormORM]):
    """
    Form repository using OrgScopedRepository.

    Forms use the CASCADE scoping pattern for org users:
    - Org-specific forms + global (NULL org_id) forms

    Role-based access control:
    - Forms with access_level="role_based" require user to have a role assigned
    - Forms with access_level="authenticated" are accessible to any authenticated user
    """

    model = FormORM
    role_table = FormRole
    role_entity_id_column = "form_id"

    async def list_forms(
        self,
        active_only: bool = True,
    ) -> list[FormORM]:
        """
        List forms with cascade scoping and role-based access.

        Uses the base class scoping and role checking automatically.
        Eager-loads the fields relationship for efficient access.

        Args:
            active_only: If True, only return active forms

        Returns:
            List of Form ORM objects with fields eager-loaded
        """
        # Build base query with cascade scoping
        query = select(self.model).options(selectinload(self.model.fields))
        query = self._apply_cascade_scope(query)

        if active_only:
            query = query.where(self.model.is_active.is_(True))

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        entities = list(result.scalars().all())

        # Filter by role access for non-superusers with role-based entities
        if not self.is_superuser:
            accessible = []
            for entity in entities:
                if await self._can_access_entity(entity):
                    accessible.append(entity)
            return accessible

        return entities

    async def list_all_in_scope(
        self,
        filter_type: OrgFilterType = OrgFilterType.ALL,
        active_only: bool = False,
    ) -> list[FormORM]:
        """
        List all forms in scope without role-based filtering.

        Used by platform admins who bypass role checks.
        Supports all filter types:
        - ALL: No org filter, show everything
        - GLOBAL_ONLY: Only forms with org_id IS NULL
        - ORG_ONLY: Only forms in the specific org (no global fallback)
        - ORG_PLUS_GLOBAL: Forms in the org + global forms

        Args:
            filter_type: How to filter by organization scope
            active_only: If True, only return active forms

        Returns:
            List of Form ORM objects with fields eager-loaded
        """
        query = select(self.model).options(selectinload(self.model.fields))

        # Apply org filtering based on filter type
        if filter_type == OrgFilterType.ALL:
            # No org filter - show everything
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            # Only global forms (org_id IS NULL)
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            # Only the specific org, NO global fallback
            if self.org_id is not None:
                query = query.where(self.model.organization_id == self.org_id)
            else:
                # Edge case: ORG_ONLY with no org_id - return nothing
                query = query.where(self.model.id.is_(None))
        elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
            # Cascade scope: org + global
            query = self._apply_cascade_scope(query)

        if active_only:
            query = query.where(self.model.is_active.is_(True))

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_form(self, form_id: UUID) -> FormORM | None:
        """
        Get form by ID with fields loaded.

        Note: This is a raw lookup without org scoping or role checks.
        Access control should be performed at the caller level.

        Args:
            form_id: Form UUID

        Returns:
            Form ORM object or None if not found
        """
        result = await self.session.execute(
            select(self.model)
            .options(selectinload(self.model.fields))
            .where(self.model.id == form_id)
        )
        return result.scalar_one_or_none()

    async def get_form_with_access_check(self, form_id: UUID) -> FormORM | None:
        """
        Get form by ID with cascade scoping and role-based access check.

        Uses the base class get() with eager loading of fields.

        Args:
            form_id: Form UUID

        Returns:
            Form ORM object if found and accessible, None otherwise
        """
        # Build query with eager loading
        query = (
            select(self.model)
            .options(selectinload(self.model.fields))
            .where(self.model.id == form_id)
        )

        # Apply cascade scoping: prioritize org-specific, then global
        if self.org_id is not None:
            # Try org-specific first
            org_query = query.where(self.model.organization_id == self.org_id)
            result = await self.session.execute(org_query)
            entity = result.scalar_one_or_none()
            if entity:
                if await self._can_access_entity(entity):
                    return entity
                return None

        # Fall back to global
        global_query = query.where(self.model.organization_id.is_(None))
        result = await self.session.execute(global_query)
        entity = result.scalar_one_or_none()

        if entity and await self._can_access_entity(entity):
            return entity
        return None
